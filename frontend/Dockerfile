# 前端镜像：构建产物交给 nginx 托管，并把 /api 反代到后端。
#
# 为什么这么做：VITE_API_BASE_URL 是构建期变量，写死后换个部署环境就得重新构建。
# 改为同源反代后，浏览器只访问当前域名下的 /api，地址与端口彻底与构建解耦。

FROM node:22-alpine AS build

WORKDIR /app
COPY package.json package-lock.json* ./
RUN npm install
COPY . .
RUN npm run build

FROM nginx:1.27-alpine

COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 5174

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD wget -q --spider http://127.0.0.1:5174/ || exit 1

CMD ["nginx", "-g", "daemon off;"]
