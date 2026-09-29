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
add_library(ktv STATIC src/api.cpp src/normalization.cpp src/sla.cpp src/cluster.cpp src/adapter.cpp src/rules.cpp src/travel.cpp src/dp.cpp src/plan.cpp)
target_include_directories(ktv PUBLIC include third_party)
target_link_libraries(ktv PUBLIC Threads::Threads)

add_executable(ktv_core src/main.cpp)
target_link_libraries(ktv_core ktv)

# Gateway read model: app riêng, không phụ thuộc pipeline.
add_library(gateway STATIC src/gateway/store.cpp src/gateway/seed.cpp)
target_include_directories(gateway PUBLIC include third_party)
target_link_libraries(gateway PUBLIC Threads::Threads)

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

# Gateway: store + seed loader.
foreach(name gateway_store gateway_seed)
  add_executable(test_${name} tests/test_${name}.cpp)
  target_link_libraries(test_${name} gateway)
  add_test(NAME ${name} COMMAND test_${name})
endforeach()

# Bất biến trên diện rộng; tùy chọn chạy thêm benchmark thật.
add_executable(test_invariants tests/test_invariants.cpp)
target_link_libraries(test_invariants ktv)
add_test(NAME invariants COMMAND test_invariants ${CMAKE_CURRENT_SOURCE_DIR}/../artifacts/fake/messages.jsonl)
