ARG BUILD_FROM
FROM ${BUILD_FROM}

RUN apk add --no-cache \
    python3 \
    py3-pip \
    py3-numpy \
    py3-opencv \
    libstdc++ \
    libgomp

WORKDIR /app

COPY requirements.txt .
RUN pip3 install --no-cache-dir -r requirements.txt --break-system-packages

COPY src/ ./src/
COPY run.sh /
RUN chmod a+x /run.sh

CMD [ "/run.sh" ]
