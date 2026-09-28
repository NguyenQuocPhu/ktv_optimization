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
add_library(ktv STATIC src/api.cpp src/normalization.cpp src/rules.cpp src/travel.cpp src/dp.cpp src/plan.cpp)
target_include_directories(ktv PUBLIC include third_party)
target_link_libraries(ktv PUBLIC Threads::Threads)

add_executable(ktv_core src/main.cpp)
target_link_libraries(ktv_core ktv)

enable_testing()
foreach(name api dp plan travel normalization)
  add_executable(test_${name} tests/test_${name}.cpp)
  target_link_libraries(test_${name} ktv)
  add_test(NAME ${name} COMMAND test_${name})
endforeach()
