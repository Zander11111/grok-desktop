#!/usr/bin/env bash
# Build the self-contained, pruned Python library dir shipped in the .deb and the tarball:
#   PySide6 (Core/Gui/Widgets/Network + Qt libs/plugins for xcb+wayland), shiboken6, markdown, pygments, python-xlib.
# PySide6 wheels are abi3 (any CPython >= 3.9), so one bundle runs on Zorin's python3.12 and Arch's python3.13+.
# Pillow is NOT bundled (it is ABI-specific): the system's python3-pil / ImageMagick does screenshot scaling.
# usage: scripts/vendor-linux.sh <out-dir>
set -euo pipefail
OUT="${1:?out dir}"
cd "$(dirname "$0")/.."
PYSIDE_VER="${PYSIDE_VER:-6.12.0}"
rm -rf "$OUT"; mkdir -p "$OUT"
PIP="${PIP:-.venv/bin/pip}"
$PIP install -q --target "$OUT" "PySide6-Essentials==$PYSIDE_VER" "shiboken6==$PYSIDE_VER" markdown pygments python-xlib
# Voice engine (sherpa-onnx: STT + Piper TTS + onnxruntime). Its extension module is ABI-specific, so ship cp311..cp313 side by side
# (distinct file names, one shared onnxruntime) -> Ubuntu 24.04/Zorin 18 (3.12), Debian 12 (3.11), Arch (3.13). Models are NOT bundled.
SHERPA_VER="${SHERPA_VER:-1.13.8}"
WT="$(mktemp -d)"
for PYV in 3.11 3.12 3.13; do
  $PIP download -q --no-deps --only-binary=:all: --python-version "$PYV" --platform manylinux2014_x86_64 \
    --platform manylinux_2_17_x86_64 -d "$WT" "sherpa-onnx==$SHERPA_VER" "sherpa-onnx-core==$SHERPA_VER"
done
for w in "$WT"/*.whl; do unzip -q -o "$w" -d "$OUT"; done
rm -rf "$WT" "$OUT"/sherpa_onnx/include "$OUT"/sherpa_onnx/cli.py "$OUT"/sherpa_onnx/lib/libsherpa-onnx-cxx-api.so
cd "$OUT"
rm -rf bin ./*.dist-info PySide6/include PySide6/typesystems PySide6/scripts PySide6/glue PySide6/*.pyi PySide6/py.typed \
  PySide6/Qt/qml PySide6/Qt/translations PySide6/Qt/metatypes PySide6/Qt/libexec PySide6/Qt/plugins/qmltooling \
  PySide6/Qt/plugins/sqldrivers PySide6/Qt/plugins/wayland-graphics-integration-server shiboken6/*.pyi \
  shiboken6_generator __pycache__
cd PySide6
rm -f designer assistant linguist lupdate lrelease qml* uic rcc svgtoolkit* pyside6-* balsam balsamui qsb \
  shiboken6* QtDesigner* QtQml* QtQuick* QtSql* QtTest* QtHelp* QtUiTools* QtPdf* QtLocation* QtScxml* QtStateMachine* \
  QtSensors* QtPositioning* QtNfc* QtRemoteObjects* QtSerialPort* QtSerialBus* QtWebChannel* QtWebSockets* QtBluetooth* \
  QtMultimedia* QtSpatialAudio* QtHttpServer* QtGraphs* QtCharts* QtDataVisualization* Qt3D* QtConcurrent* QtXml* \
  QtPrintSupport* QtOpenGL* QtSvg* QtTextToSpeech* QtQuick3D* QtNetworkAuth* QtVirtualKeyboard* QtShaderTools* \
  QtStateMachine* QtSql* QtDBus* QtAxContainer* QtSpeech* QtAsyncio* 2>/dev/null || true
cd Qt/lib
rm -f libQt6Quick* libQt6Qml* libQt6Designer* libQt6Labs* libQt6Pdf* libQt63D* libQt6Sql* libQt6Test* libQt6Help* \
  libQt6WaylandCompositor* libQt6Multimedia* libQt6Charts* libQt6DataVis* libQt6Graphs* libQt6Location* libQt6Positioning* \
  libQt6Sensors* libQt6Bluetooth* libQt6Nfc* libQt6RemoteObjects* libQt6Scxml* libQt6StateMachine* libQt6WebSockets* \
  libQt6WebChannel* libQt6SerialPort* libQt6SerialBus* libQt6Concurrent* libQt6Xml* libQt6Spatial* libQt6ShaderTools* \
  libQt6HttpServer* libQt6TextToSpeech* libQt6VirtualKeyboard* libQt6NetworkAuth* libQt6UiTools* libQt6PrintSupport* \
  libQt6OpenGLWidgets* libQt6Svg* libQt6QuickControls* libQt6Core5Compat* libQt6ExampleIcons* libQt6Designer*
rm -f libQt6Lottie* libQt6EglFS* libQt6EglFs* libQt6WaylandEglCompositor*   # Qt links its own ICU (libicu*): keep it
cd ../plugins
rm -rf designer egldeviceintegrations printsupport vectorimageformats
cd ../..
rm -rf doc svgtoqml QtLabsStyleKit* libpyside6qml* support/generate_pyi.py
cd ..
find . -name '__pycache__' -type d -prune -exec rm -rf {} +
echo "vendor dir: $(du -sh . | cut -f1)"
