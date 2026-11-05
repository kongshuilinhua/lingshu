from __future__ import annotations

import base64
import binascii
import os
import re
import tempfile
import uuid
from pathlib import Path
from zipfile import BadZipFile, ZipFile

from sqlalchemy.orm import Session

from core.config import get_settings
from core.db.models import Upload
from core.integrations.storage import object_storage

# 🧠 魔鬼数字与前沿技术参数：支持的多模态文件 mime-type 集合划分。
# 区分 image 与 document 可以让系统对于不同资产实施差异化的编排链路（如多模态 Vision vs RAG 检索分流）。
IMAGE_TYPES = {"image/png", "image/jpeg", "image/webp", "image/gif"}
TEXT_TYPES = {"text/plain", "text/markdown", "application/markdown", "text/csv"}
# OOXML 家族（docx/xlsx/pptx）共用的 mime-type 前缀
_OOXML_PREFIX = "application/vnd.openxmlformats-officedocument."
XLSX_TYPE = _OOXML_PREFIX + "spreadsheetml.sheet"
PPTX_TYPE = _OOXML_PREFIX + "presentationml.presentation"
DOC_TYPES = TEXT_TYPES | {
    "application/pdf",
    _OOXML_PREFIX + "wordprocessingml.document",
    XLSX_TYPE,
    PPTX_TYPE,
}
LANGCHAIN_EXTRA_SUFFIXES = {".html", ".htm"}
LANGCHAIN_EXTRA_TYPES = {"text/html", "application/xhtml+xml"}


def create_upload(db: Session, *, workspace_id: int, user_id: int, filename: str, content_type: str, content_base64: str) -> Upload:
    """
    创建上传文件并自动解析提取其多模态内容或纯文本。

    🎯 意图与工程大局观：
        本函数为平台多模态资产接入与 RAG 文本入库的“安检口”和“分流器”。
        - 对于图片资产，将其包装为规范的 data_url 直接传递给多模态大模型进行视觉推理。
        - 对于文档资产，触发提取管道拉取其核心文本，持久化为 RAG 或 Context 提示词的一部分。
    
    🛡️ 防御性编程与大模型兜底：
        - 限制文件尺寸（settings.upload_max_bytes），防止大体积文件攻击引起服务端 OOM。
        - 限制只有 image 和 document 两类资源准入，其余归于 unknown 抛出，从源头封堵未知漏洞。
        - 对文档提取出的纯文本进行严格的 `\x00` 空字符过滤，规避数据库在保存时的 Null-byte 物理截断报错。
        - 对提取结果进行硬性长度限制 `[:20000]`，防止由于单个超长 PDF 塞爆大模型上下文窗口或在 Embedding 阶段产生超长 Token 费用。
    """
    settings = get_settings()
    raw = _decode_base64(content_base64)
    if len(raw) > settings.upload_max_bytes:
        raise ValueError(f"Upload file cannot exceed {settings.upload_max_bytes // (1024 * 1024)}MB")
    kind = _kind(content_type, filename)
    if kind not in {"image", "document"}:
        raise ValueError("Only image and document uploads are supported")
    upload_id = f"upload_{uuid.uuid4().hex}"
    data_url = ""
    storage_key = ""
    text = ""
    if kind == "image":
        # 🎯 优先把图片字节存对象库（仅在 DB 留 storage_key 引用），DB 不再背 base64 大列。
        #    对象库未配置 / 不可用 / 写入失败时回退到 base64 直存 DB（现状），保证单机零配置可用。
        if object_storage.available:
            key = f"images/{workspace_id}/{upload_id}"
            if object_storage.put(key, raw, content_type):
                storage_key = key
        if not storage_key:
            data_url = f"data:{content_type};base64,{base64.b64encode(raw).decode('ascii')}"
    else:
        text = sanitize_extracted_text(extract_document_text(filename, content_type, raw))
        if not text.strip():
            raise ValueError("Document text could not be extracted")
    upload = Upload(
        id=upload_id,
        workspace_id=workspace_id,
        user_id=user_id,
        filename=Path(filename).name,
        content_type=content_type,
        kind=kind,
        data_url=data_url,
        storage_key=storage_key,
        text=text[:20000],
        size=len(raw),
    )
    db.add(upload)
    db.commit()
    db.refresh(upload)
    return upload


def upload_payload(upload: Upload) -> dict:
    """
    序列化上传实体，返回前端交互所需的最小信息载荷。

    🧠 魔鬼数字：`text_preview` 截断长度限制在 240 字符。
        作为列表预览时既能为用户提供足够的上下文认知，又能避免返回完整文本拖慢前端渲染与网络 I/O 吞吐。
    """
    return {
        "id": upload.id,
        "filename": upload.filename,
        "content_type": upload.content_type,
        "type": upload.kind,
        "size": upload.size,
        "preview_url": resolve_image_data_url(upload) if upload.kind == "image" else "",
        "text_preview": (upload.text or "")[:240],
    }


def resolve_image_data_url(upload: Upload) -> str:
    """
    取得图片的 data URL（base64）。

    🎯 优先用 DB 内联 data_url（现状 / 对象库不可用时的回退）；否则从对象库取回字节按需转 base64。
        刻意返回 base64 而非对象库直链——外部多模态 LLM 厂商在公网，够不到内网 MinIO，必须把字节带过去；
        前端 <img> 也因此不需要对受保护的对象库做带鉴权请求。供 LLM 视觉调用与上传预览复用。
    """
    if upload.data_url:
        return upload.data_url
    if upload.storage_key and object_storage.available:
        data = object_storage.get(upload.storage_key)
        if data:
            return f"data:{upload.content_type};base64,{base64.b64encode(data).decode('ascii')}"
    return ""


def get_workspace_uploads(db: Session, *, workspace_id: int, upload_ids: list[str]) -> list[Upload]:
    """
    批量获取指定工作区下的上传资源并校验所有权。

    🛡️ 防御性编程（多租户越权防范）：
        即使传入合法的 UUID，在查询时也必须携带当前 Workspace_id 联合过滤。
        最后采用两集合比对，一旦发现请求的 IDs 和实际检索出的 IDs 不等，立刻抛出
        "Upload not found or not accessible" 异常，从底层物理掐断“水平越权”获取他人资产的行为。
    """
    if not upload_ids:
        return []
    uploads = (
        db.query(Upload)
        .filter(Upload.workspace_id == workspace_id, Upload.id.in_(upload_ids))
        .order_by(Upload.created_at.asc())
        .all()
    )
    found_ids = {upload.id for upload in uploads}
    requested_ids = set(upload_ids)
    if found_ids != requested_ids:
        raise ValueError("Upload not found or not accessible")
    return uploads


def extract_document_text(filename: str, content_type: str, raw: bytes) -> str:
    """
    自适应文档提取总枢纽。

    🎯 意图与工程大局观：
        依据文件后缀与 mime-type 自动调度正确的解码器。
        支持纯文本、Markdown、CSV、PDF 和 DOCX。
    """
    suffix = Path(filename).suffix.lower()
    if content_type in TEXT_TYPES or suffix in {".txt", ".md", ".markdown", ".csv"}:
        return decode_bytes(raw)
    if content_type == "application/pdf" or suffix == ".pdf":
        return _extract_pdf_text(raw)
    if content_type == _OOXML_PREFIX + "wordprocessingml.document" or suffix == ".docx":
        return _extract_docx_text(raw)
    if content_type == XLSX_TYPE or suffix == ".xlsx":
        return _extract_xlsx_text(raw)
    if content_type == PPTX_TYPE or suffix == ".pptx":
        return _extract_pptx_text(raw)
    if get_settings().ingest_langchain_loaders and (
        suffix in LANGCHAIN_EXTRA_SUFFIXES or content_type in LANGCHAIN_EXTRA_TYPES
    ):
        return _extract_via_langchain(filename, raw)
    raise ValueError("Unsupported document type")


def _extract_via_langchain(filename: str, raw: bytes) -> str:
    """通过 LangChain loader 提取额外文档类型，并确保 Windows 临时文件可被重新打开。"""
    suffix = Path(filename).suffix.lower()
    temp_path = ""
    try:
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as temp_file:
            temp_file.write(raw)
            temp_path = temp_file.name
        try:
            if suffix in {".html", ".htm"}:
                from langchain_community.document_loaders import BSHTMLLoader

                loader = BSHTMLLoader(
                    temp_path,
                    open_encoding="utf-8",
                    bs_kwargs={"features": "lxml"},
                    get_text_separator="\n",
                )
            else:
                raise ValueError(f"Unsupported LangChain loader suffix: {suffix}")
            documents = loader.load()
            return "\n".join(document.page_content for document in documents)
        finally:
            if temp_path:
                os.unlink(temp_path)
    except Exception as exc:
        raise ValueError(f"LangChain document extraction failed for {suffix or 'unknown'}") from exc


def sanitize_extracted_text(text: str) -> str:
    """
    清洗过滤提取后的文本，剔除 Null 字节 `\x00`，保证数据库及下游大模型在序列化/传输时安全稳定。
    """
    return str(text or "").replace("\x00", "")


def decode_bytes(raw: bytes) -> str:
    """字节解码：utf-8 严格 → gbk 严格(中文 Windows 常见) → charset_normalizer 探测 → utf-8 replace 兜底。

    🧠 为什么显式试 gbk：charset_normalizer 对短中文文本(几个字)探测不稳，而本项目大量面对
    中文 Windows 的 GBK/GB2312 文本，故在概率探测前先尝试严格 gbk（非法字节会抛错，不会误吞）。
    """
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        pass
    try:
        return raw.decode("gbk")
    except UnicodeDecodeError:
        pass
    try:
        from charset_normalizer import from_bytes

        best = from_bytes(raw).best()
        if best is not None:
            return str(best)
    except Exception:
        pass
    return raw.decode("utf-8", errors="replace")


def _decode_base64(content_base64: str) -> bytes:
    """
    防爆防注入式 Base64 解码器。
    
    🛡️ 防御性设计：
        支持解析带有 `data:...;base64,` 前缀的 Data URL 格式，也能处理纯 base64 裸码。
        强制开启 `validate=True` 校验，一旦有格式损坏的脏数据直接抛出 ValueError，杜绝非法二进制溢出攻击风险。
    """
    payload = content_base64.split(",", 1)[1] if content_base64.startswith("data:") and "," in content_base64 else content_base64
    try:
        return base64.b64decode(payload, validate=True)
    except binascii.Error as exc:
        raise ValueError("Invalid base64 upload payload") from exc


def _kind(content_type: str, filename: str) -> str:
    """多级 fallback 的资源归类判定器。"""
    suffix = Path(filename).suffix.lower()
    if content_type in IMAGE_TYPES or suffix in {".png", ".jpg", ".jpeg", ".webp", ".gif"}:
        return "image"
    if content_type in DOC_TYPES or suffix in {".txt", ".md", ".markdown", ".csv", ".pdf", ".docx", ".xlsx", ".pptx"}:
        return "document"
    if get_settings().ingest_langchain_loaders and (
        content_type in LANGCHAIN_EXTRA_TYPES or suffix in LANGCHAIN_EXTRA_SUFFIXES
    ):
        return "document"
    return "unknown"


def _extract_pdf_text(raw: bytes) -> str:
    """
    高度健壮的 PDF 文本解析器。

    🛡️ 防御性编程与大模型兜底：
        - 优先导入第三方 `pypdf` 进行页层级（Page-by-page）的物理文本提取。
        - 设计了高强度 Fallback（平滑降级）：
          如果 pypdf 模块由于环境原因未安装，或者解析加密/损坏的 PDF 失败抛出异常，
          系统会自动切入备用路径：以 `latin-1` 强制解码，使用正则抓取 PDF 未压缩特有的 `(text) Tj` 标记，
          或用最后一层防御：正则合并所有空白字符并强行切取前 4000 字符，保证系统能够最低限度地读到文件内容，绝不在提取阶段发生物理 Crash。
    """
    try:
        from io import BytesIO
        from pypdf import PdfReader

        reader = PdfReader(BytesIO(raw))
        pages = []
        for page in reader.pages:
            page_text = page.extract_text()
            if page_text:
                pages.append(page_text)
        return "\n".join(pages) if pages else ""
    except Exception:
        # Fallback: basic extraction for simple uncompressed PDFs
        text = raw.decode("latin-1", errors="ignore")
        chunks = re.findall(r"\(([^()]*)\)\s*Tj", text)
        if chunks:
            return "\n".join(chunks)
        return re.sub(r"\s+", " ", text)[:4000]


def _extract_xlsx_text(raw: bytes) -> str:
    """
    Excel (xlsx) 纯文本提取器，同样只用标准库解 OOXML。

    🎯 与 DOCX 提取器保持同一思路：xlsx 是 zip，工作表在 `xl/worksheets/sheetN.xml`，
    字符串常集中在 `xl/sharedStrings.xml` 里按索引引用。

    🧠 输出形态：每个工作表一段，行内单元格用 ` | ` 分隔，空行丢弃。
        RAG 需要的是「同一行的单元格仍然挨在一起」——比如「产品名 | 价格 | 库存」，
        拆成独立行会让检索片段丢掉列语义。单元格坐标不参与输出，避免 `A1`、`B2` 这类噪音进向量。
    """
    try:
        from io import BytesIO

        with ZipFile(BytesIO(raw)) as archive:
            names = archive.namelist()
            shared = _xlsx_shared_strings(archive) if "xl/sharedStrings.xml" in names else []
            # 工作表按文件名排序：sheet1, sheet2 … sheet10
            sheets = sorted(
                (name for name in names if re.match(r"xl/worksheets/sheet\d+\.xml$", name)),
                key=lambda name: int(re.search(r"sheet(\d+)\.xml$", name).group(1)),
            )
            if not sheets:
                raise ValueError("Invalid XLSX document")
            blocks = []
            for index, sheet_name in enumerate(sheets, start=1):
                xml = archive.read(sheet_name).decode("utf-8", errors="replace")
                rows = _xlsx_rows(xml, shared)
                if rows:
                    blocks.append(f"## 工作表 {index}\n" + "\n".join(rows))
            return "\n\n".join(blocks)
    except (KeyError, BadZipFile) as exc:
        raise ValueError("Invalid XLSX document") from exc


def _xlsx_shared_strings(archive: ZipFile) -> list[str]:
    """读取共享字符串表；解析失败返回空表，让调用方退化为只取单元格原始值。"""
    try:
        xml = archive.read("xl/sharedStrings.xml").decode("utf-8", errors="replace")
    except KeyError:
        return []
    return [
        _strip_xml("".join(re.findall(r"<t[^>]*>(.*?)</t>", item, re.DOTALL)))
        for item in re.findall(r"<si[ >].*?</si>", xml, re.DOTALL)
    ]


def _xlsx_rows(xml: str, shared: list[str]) -> list[str]:
    """把一张工作表拆成「单元格用 | 连接」的文本行。"""
    rows = []
    for row_xml in re.findall(r"<row[ >].*?</row>", xml, re.DOTALL):
        cells = []
        for cell_xml in re.findall(r"<c[ >].*?(?:</c>|/>)", row_xml, re.DOTALL):
            cell_type = re.search(r'\bt="([^"]+)"', cell_xml)
            value = ""
            if cell_type and cell_type.group(1) == "inlineStr":
                value = _strip_xml("".join(re.findall(r"<t[^>]*>(.*?)</t>", cell_xml, re.DOTALL)))
            else:
                raw_value = re.search(r"<v[^>]*>(.*?)</v>", cell_xml, re.DOTALL)
                if raw_value:
                    text = _strip_xml(raw_value.group(1))
                    if cell_type and cell_type.group(1) == "s":
                        try:
                            value = shared[int(text)]
                        except (ValueError, IndexError):
                            value = ""
                    else:
                        value = text
            value = value.strip()
            if value:
                cells.append(value)
        if cells:
            rows.append(" | ".join(cells))
    return rows


def _extract_pptx_text(raw: bytes) -> str:
    """
    PowerPoint (pptx) 纯文本提取器。

    🎯 幻灯片正文在 `ppt/slides/slideN.xml`，文字节点是 DrawingML 的 `<a:t>`。
        按幻灯片编号输出，保留「第几页」这个结构信息——演示文稿的上下文强依赖页序。
    备注页（notesSlides）单独拼接在后面：很多汇报的实际信息量在备注里。
    """
    try:
        from io import BytesIO

        with ZipFile(BytesIO(raw)) as archive:
            names = archive.namelist()
            slides = sorted(
                (name for name in names if re.match(r"ppt/slides/slide\d+\.xml$", name)),
                key=lambda name: int(re.search(r"slide(\d+)\.xml$", name).group(1)),
            )
            if not slides:
                raise ValueError("Invalid PPTX document")
            blocks = []
            for index, slide_name in enumerate(slides, start=1):
                xml = archive.read(slide_name).decode("utf-8", errors="replace")
                lines = [line.strip() for line in re.findall(r"<a:t[^>]*>(.*?)</a:t>", xml, re.DOTALL)]
                body = "\n".join(_strip_xml(line) for line in lines if line.strip())
                if body:
                    blocks.append(f"## 第 {index} 页\n{body}")
            notes = []
            for name in sorted(name for name in names if re.match(r"ppt/notesSlides/notesSlide\d+\.xml$", name)):
                xml = archive.read(name).decode("utf-8", errors="replace")
                lines = [line.strip() for line in re.findall(r"<a:t[^>]*>(.*?)</a:t>", xml, re.DOTALL)]
                text = "\n".join(_strip_xml(line) for line in lines if line.strip())
                if text:
                    notes.append(text)
            if notes:
                blocks.append("## 备注\n" + "\n\n".join(notes))
            return "\n\n".join(blocks)
    except (KeyError, BadZipFile) as exc:
        raise ValueError("Invalid PPTX document") from exc


def _extract_docx_text(raw: bytes) -> str:
    """
    极速、无依赖的 Word (docx) 纯文本提取器。

    ⚡ 边界与性能思考：
        - 放弃引入复杂的 python-docx 等大型外部库，利用 DOCX 本质上是 zip 压缩包的特性，
          直接用内置 `ZipFile` 内存解压，高速读取其中承载正文的 `word/document.xml`。
        - 空间复杂度极佳：流式解压读取，避免了大段二进制文件的内存冗余复制。
        - 设计了二级 Fallback：优先采用 `<w:p>` 标签对段落进行物理分割；
          如果文档结构遭到破坏没有匹配到 paragraph，则降级为直接匹配全局 `<w:t>` 文本。
    """
    try:
        from io import BytesIO

        with ZipFile(BytesIO(raw)) as archive:
            xml = archive.read("word/document.xml").decode("utf-8", errors="replace")
    except (KeyError, BadZipFile) as exc:
        raise ValueError("Invalid DOCX document") from exc
    # Group text runs by paragraph.
    paragraphs = re.findall(r"<w:p[ >].*?</w:p>", xml, re.DOTALL)
    if paragraphs:
        lines = []
        for p in paragraphs:
            runs = re.findall(r"<w:t[^>]*>(.*?)</w:t>", p)
            line = "".join(_strip_xml(item) for item in runs).strip()
            if line:
                lines.append(line)
        return "\n".join(lines)
    # Fallback: flat extraction for malformed documents.
    runs = re.findall(r"<w:t[^>]*>(.*?)</w:t>", xml)
    return "\n".join(_strip_xml(item) for item in runs)


def _strip_xml(value: str) -> str:
    """清除并还原 XML 内部的基础转义标签（如 `&lt;` 转换为 `<` 等），防止乱码或特殊实体遗漏。"""
    return (
        value.replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&amp;", "&")
        .replace("&quot;", '"')
        .replace("&apos;", "'")
    )
