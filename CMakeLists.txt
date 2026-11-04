cmake_minimum_required(VERSION 3.25)
project(savior_release LANGUAGES NONE)

find_package(Python3 3.12 REQUIRED COMPONENTS Interpreter)
set(SAVIOR_RELEASE_CONFIG "${CMAKE_SOURCE_DIR}/scripts/release/local.example.json" CACHE FILEPATH "Release configuration (no secrets)")
set(SAVIOR_RELEASE_PARTS "${CMAKE_SOURCE_DIR}/.cache/parts" CACHE PATH "Prebuilt platform stages")
set(SAVIOR_RELEASE_OUTPUT "${CMAKE_SOURCE_DIR}/.cache/local-release" CACHE PATH "New private candidate directory")

foreach(stage plan preflight sign notarize package validate candidate)
  add_custom_target(release_${stage}
    COMMAND "${Python3_EXECUTABLE}" "${CMAKE_SOURCE_DIR}/scripts/release/local.py" "${stage}"
      --config "${SAVIOR_RELEASE_CONFIG}" --parts "${SAVIOR_RELEASE_PARTS}" --output "${SAVIOR_RELEASE_OUTPUT}"
    WORKING_DIRECTORY "${CMAKE_SOURCE_DIR}"
    VERBATIM)
endforeach()

enable_testing()
add_test(NAME release_tooling COMMAND "${Python3_EXECUTABLE}" -m unittest discover -s scripts/release
  WORKING_DIRECTORY "${CMAKE_SOURCE_DIR}")
add_custom_target(release_test
  COMMAND "${CMAKE_CTEST_COMMAND}" --output-on-failure VERBATIM)
