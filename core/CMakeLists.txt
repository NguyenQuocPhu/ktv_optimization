cmake_minimum_required(VERSION 3.14)
project(ktv_core CXX)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
if(NOT CMAKE_BUILD_TYPE)
  set(CMAKE_BUILD_TYPE Release)
endif()
add_compile_options(-Wall -Wextra)
find_package(Threads REQUIRED)  # cpp-httplib (gọi OSRM) cần pthread.

# Mỗi module một cặp include/ktv/<module>.hpp + src/<module>.cpp.
add_library(ktv STATIC src/api.cpp src/normalization.cpp src/sla.cpp src/cluster.cpp
                         src/adapter/envelope.cpp src/adapter/file.cpp
                         src/rules.cpp src/travel.cpp src/dp.cpp src/plan.cpp)
target_include_directories(ktv PUBLIC include third_party)
target_link_libraries(ktv PUBLIC Threads::Threads)

add_executable(ktv_core src/cli/main.cpp)
target_link_libraries(ktv_core ktv)

# Gateway read model: app riêng, không phụ thuộc pipeline.
find_package(PkgConfig QUIET)
if(PkgConfig_FOUND)
  pkg_check_modules(HIREDIS QUIET hiredis)
endif()

add_library(gateway STATIC src/gateway/store.cpp src/gateway/seed.cpp src/gateway/server.cpp)
target_include_directories(gateway PUBLIC include third_party)
target_link_libraries(gateway PUBLIC Threads::Threads)
if(HIREDIS_FOUND)
  target_sources(gateway PRIVATE src/gateway/redis_store.cpp)
  target_include_directories(gateway PRIVATE ${HIREDIS_INCLUDE_DIRS})
  target_link_libraries(gateway PRIVATE ${HIREDIS_LIBRARIES})
  target_compile_definitions(gateway PUBLIC KTV_WITH_REDIS=1)
  message(STATUS "gateway: RedisRouteStore BẬT (hiredis)")
else()
  message(STATUS "gateway: RedisRouteStore TẮT (thiếu hiredis)")
endif()

add_executable(ktv_gateway src/gateway/main.cpp)
target_link_libraries(ktv_gateway gateway)

# Kafka worker: đọc topic IN. Cần librdkafka (pkg-config rdkafka);
# thiếu thì chỉ build phần config + test, binary ktv_worker tắt.
if(PkgConfig_FOUND)
  pkg_check_modules(RDKAFKA QUIET rdkafka)
endif()

add_library(kafka STATIC src/kafka/config.cpp)
target_include_directories(kafka PUBLIC include)
target_link_libraries(kafka PUBLIC Threads::Threads)
if(RDKAFKA_FOUND)
  target_sources(kafka PRIVATE src/kafka/consumer.cpp)
  target_include_directories(kafka PRIVATE ${RDKAFKA_INCLUDE_DIRS})
  target_link_libraries(kafka PUBLIC ${RDKAFKA_LIBRARIES})
  target_compile_definitions(kafka PUBLIC KTV_WITH_KAFKA=1)
  add_executable(ktv_worker src/kafka/main.cpp)
  target_link_libraries(ktv_worker kafka ktv)
  message(STATUS "kafka: ktv_worker BẬT (rdkafka ${RDKAFKA_VERSION})")
else()
  message(STATUS "kafka: ktv_worker TẮT (thiếu librdkafka; cài: apt install librdkafka-dev)")
endif()

enable_testing()
foreach(name api dp plan travel normalization sla cluster adapter pipeline)
  add_executable(test_${name} tests/test_${name}.cpp)
  target_link_libraries(test_${name} ktv)
  add_test(NAME ${name} COMMAND test_${name})
endforeach()

# CLI end-to-end cần đường dẫn binary ktv_core.
add_executable(test_cli tests/test_cli.cpp)
target_link_libraries(test_cli ktv)
add_test(NAME cli COMMAND test_cli $<TARGET_FILE:ktv_core>)

# Gateway: store + seed loader + HTTP.
foreach(name gateway_store gateway_seed gateway_server)
  add_executable(test_${name} tests/test_${name}.cpp)
  target_link_libraries(test_${name} gateway)
  add_test(NAME ${name} COMMAND test_${name})
endforeach()

# Kafka config: parse/validate .env — không cần librdkafka nên luôn chạy.
add_executable(test_kafka_config tests/test_kafka_config.cpp)
target_link_libraries(test_kafka_config kafka)
add_test(NAME kafka_config COMMAND test_kafka_config)

# Redis store: chỉ build khi có hiredis; test tự skip nếu không kết nối được Redis.
if(HIREDIS_FOUND)
  add_executable(test_gateway_redis tests/test_gateway_redis.cpp)
  target_include_directories(test_gateway_redis PRIVATE ${HIREDIS_INCLUDE_DIRS})
  target_link_libraries(test_gateway_redis gateway ${HIREDIS_LIBRARIES})
  add_test(NAME gateway_redis COMMAND test_gateway_redis)
  set_tests_properties(gateway_redis PROPERTIES ENVIRONMENT "KTV_TEST_REDIS=127.0.0.1:6379")
endif()

# Bất biến trên diện rộng; tùy chọn chạy thêm benchmark thật.
add_executable(test_invariants tests/test_invariants.cpp)
target_link_libraries(test_invariants ktv)
add_test(NAME invariants COMMAND test_invariants ${CMAKE_CURRENT_SOURCE_DIR}/../artifacts/fake/messages.jsonl)
