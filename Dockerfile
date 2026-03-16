FROM node:20-slim AS frontend
WORKDIR /app/frontend
COPY frontend/package.json .
RUN npm install
COPY frontend/src/ ./src/
COPY frontend/index.html .
COPY frontend/vite.config.js .
RUN npm run build

FROM python:3.11-slim
WORKDIR /app
COPY backend/requirements.txt .
RUN pip3 install -r requirements.txt
COPY backend/ ./backend/
COPY --from=frontend /app/frontend/dist ./frontend/dist
EXPOSE 8000
CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
