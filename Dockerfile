FROM node:20-slim

RUN npm install -g wrangler

RUN apt-get update && apt-get install -y python3 python3-pip && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip3 install -r requirements.txt --break-system-packages

COPY main.py .

CMD ["python3", "main.py"]
