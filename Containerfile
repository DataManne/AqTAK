FROM python:3.12-slim

WORKDIR /app

COPY aq_to_atak.py /app/aq_to_atak.py

RUN pip install --no-cache-dir requests

EXPOSE 9000/tcp

CMD ["python", "-u", "/app/aq_to_atak.py"]
