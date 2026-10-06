FROM python:3.12-slim

WORKDIR /app

COPY aq_to_atak.py /app/aq_to_atak.py

RUN pip install --no-cache-dir requests

CMD ["python", "-u", "/app/aq_to_atak.py"]
