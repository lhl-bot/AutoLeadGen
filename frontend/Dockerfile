FROM node:20-alpine

WORKDIR /app

# Use China npm mirror for faster installs
RUN npm config set registry https://registry.npmmirror.com

# Install dependencies
COPY package.json package-lock.json* ./
RUN npm ci --production=false

# Copy source and build
COPY . .
ENV NEXT_PUBLIC_API_BASE_URL=""
RUN npm run build

EXPOSE 8888

CMD ["npm", "start", "--", "-p", "8888"]
