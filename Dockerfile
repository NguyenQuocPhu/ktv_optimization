# Image chạy 3 binary của core: ktv_worker (mặc định), ktv_gateway, ktv_core.
#   docker build -t ktv-core .
#   docker run --rm --env-file .env ktv-core                          # worker, /healthz ở 8081
#   docker run --rm -p 8080:8080 ktv-core ktv_gateway --port 8080 --redis redis:6379
# Build chạy luôn ctest: test đỏ thì image không được tạo.
FROM ubuntu:22.04 AS build
RUN apt-get update \
 && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
      build-essential cmake pkg-config libhiredis-dev librdkafka-dev \
 && rm -rf /var/lib/apt/lists/*
WORKDIR /src
COPY core core
RUN cmake -S core -B build -DCMAKE_BUILD_TYPE=Release \
 && cmake --build build -j"$(nproc)" \
 && ctest --test-dir build --output-on-failure

FROM ubuntu:22.04
RUN apt-get update \
 && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends libhiredis0.14 librdkafka1 \
 && rm -rf /var/lib/apt/lists/* \
 && useradd --system --no-create-home ktv
COPY --from=build /src/build/ktv_worker /src/build/ktv_gateway /src/build/ktv_core /usr/local/bin/
USER ktv
# Cấu hình KAFKA_* qua biến môi trường (--env-file); không có file .env trong image.
CMD ["ktv_worker", "--env", "", "--health-port", "8081"]
