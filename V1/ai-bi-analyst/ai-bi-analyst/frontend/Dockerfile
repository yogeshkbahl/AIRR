FROM node:22-alpine

WORKDIR /app
COPY package.json package-lock.json* ./
RUN npm install --no-audit --no-fund

COPY . .

ENV VITE_API_TARGET=http://backend:8000
EXPOSE 5173

# Dev server so the app is editable inside the container; run `npm run build`
# and serve dist/ behind your own web server for production.
CMD ["npm", "run", "dev", "--", "--host", "0.0.0.0"]
