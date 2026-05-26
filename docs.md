# AI Vehicle & License Plate Recognition System

# 1. Giới Thiệu & Yêu Cầu Bài Toán

Hệ thống AI realtime được thiết kế để làm quen với giao thức truyền thông MQTT và xử lý chuỗi luồng stream AI bao gồm:
- **Làm quen với MQTT:**
  - Lấy danh sách camera (Camera Discovery) qua MQTT.
  - Nhận vùng Polygon/Zone được vẽ trực tiếp trên Web thông qua MQTT.
  - Chỉ xử lý/detect đối tượng nằm trong vùng Polygon/Zone đó.
  - Đẩy tọa độ bounding box (bbox) lên giao diện Web thời gian thực bằng MQTT.
- **Bài toán xử lý AI chia làm các Phase:**
  - **Phase 1:** Nhận diện phương tiện (Vehicle Detection).
  - **Phase 2:** Phát hiện biển số xe (License Plate Detection).
  - **Phase 3:** Nhận diện ký tự biển số xe (License Plate OCR - Optional).
- **Yêu cầu kỹ thuật đặc biệt:**
  - **BBox của xe và biển số xe sẽ phải đi cùng hoặc gần nhau nhất** (Thiết lập liên kết không gian chặt chẽ hoặc tracking/nested ROI).
  - **Chạy tối ưu với 1 camera** (nhưng cấu trúc đa luồng sẵn sàng mở rộng).
  - **Lưu ý quan trọng:** Khi cấu hình và chạy chương trình, bắt buộc chọn mã `camera_code` duy nhất để **tránh những camera mà người khác đang sử dụng** nhằm tránh xung đột dữ liệu trên Broker chung.

Hệ thống sử dụng:
- GStreamer (để giải mã luồng RTSP tối ưu độ trễ)
- YOLO11 (Nhận dạng phương tiện và biển số xe)
- PaddleOCR (Nhận dạng ký tự biển số - Phase 3)
- CUDA GPU Acceleration
- Multi-thread Architecture & Paho MQTT client

Hiện tại hệ thống ưu tiên:
```text
1 Camera Realtime Processing
```

nhưng kiến trúc đã được thiết kế để có thể mở rộng multi-camera trong tương lai.

---

# 2. Kiến Trúc Tổng Thể

## Pipeline Realtime

```text
RTSP Camera (Được lọc từ danh sách Camera MQTT)
    ↓
GStreamer Decode
    ↓
Frame Queue
    ↓
Polygon Zone từ Web (nhận qua MQTT)
    ↓
Vehicle Detection (Phase 1 - chỉ giữ xe nằm trong polygon)
    ↓
Vehicle Tracking (gán track_id cho xe, tránh OCR lặp)
    ↓
Crop Vehicle ROI theo track_id
    ↓
License Plate Detection (Phase 2 - tìm biển số trong vùng crop xe)
    ↓
Crop Plate
    ↓
PaddleOCR (Phase 3 - Optional - nhận diện ký tự biển số)
    ↓
MQTT Bbox Publish (Gửi dữ liệu JSON lên Broker)
    ↓
Web Frontend (Nhận MQTT JSON và vẽ đè bbox lên giao diện)
```

---

# 3. Công Nghệ Sử Dụng

## Video Processing & MQTT Communication
- GStreamer (RTSP decoding)
- RTSP
- MQTT Broker (paho-mqtt) để lấy cấu hình camera/vùng và đẩy dữ liệu bbox

## AI Processing
- YOLO11
- PaddleOCR
- CUDA
- TensorRT Ready

## Tracking
- ByteTrack
- DeepSORT

---

# 4. GStreamer Pipeline

Hệ thống sử dụng GStreamer để:
- decode video realtime
- giảm latency
- tối ưu streaming
- hỗ trợ hardware acceleration

## Decode Pipeline

Ví dụ pipeline RTSP:

```bash
rtspsrc location=rtsp://CAMERA_URL latency=0 drop-on-latency=true !
rtph264depay !
h264parse !
avdec_h264 !
videoconvert !
appsink sync=false max-buffers=1 drop=true
```

---

## Low Latency Configuration

Các cấu hình tối ưu realtime:

```text
rtspsrc: latency=0, drop-on-latency=true
appsink: sync=false, max-buffers=1, drop=true
```

Mục tiêu:
- giảm buffer
- giảm delay
- tránh frame backlog

---

# 5. Kiến Trúc Đa Luồng

Hệ thống được thiết kế theo mô hình multi-thread để:
- tránh block pipeline
- tối ưu FPS
- tăng khả năng mở rộng

---

# 6. Multi-thread Pipeline

## Thread 1 — Decode Thread

Nhiệm vụ:
- nhận RTSP stream
- decode video bằng GStreamer
- push frame vào queue

```text
RTSP
    ↓
Decode
    ↓
Frame Queue
```

---

## Thread 2 — AI Detection Thread

Nhiệm vụ:
- lấy frame từ queue
- đọc polygon zone đã nhận từ Web qua MQTT
- YOLO detect vehicle
- chỉ giữ vehicle có tâm bbox nằm trong polygon zone
- tracking vehicle để gán `track_id`
- crop Vehicle ROI theo từng `track_id`
- YOLO detect license plate trong Vehicle ROI

```text
Frame
    ↓
Vehicle Detection
    ↓
Polygon Filter by Web Zone
    ↓
Vehicle Tracking
    ↓
Crop Vehicle ROI by track_id
    ↓
Plate Detection inside Vehicle ROI
```

---

## Thread 3 — OCR Thread

Nhiệm vụ:
- crop biển số
- preprocess image
- PaddleOCR recognition

```text
Plate Crop
    ↓
Preprocess
    ↓
PaddleOCR
```

---

## Thread 4 — MQTT Publisher Thread

Nhiệm vụ:
- Lấy kết quả AI từ Queue
- Lọc tọa độ đối tượng dựa theo vùng Polygon/Zone tương ứng của camera
- Đóng gói dữ liệu JSON và publish lên MQTT Broker thời gian thực

```text
AI Result
    ↓
Polygon Filter
    ↓
MQTT Publish
```

---

# 7. Queue Architecture

Các thread giao tiếp bằng queue:

```text
Decode Queue
AI Queue
OCR Queue
Publish Queue (MQTT Queue)
```

Ưu điểm:
- tránh blocking
- ổn định realtime
- dễ scale

---

# 8. Vehicle Detection

YOLO detect:
- car
- motorcycle
- truck
- bus

Mục tiêu:
- giảm vùng tìm biển số
- tăng accuracy
- giảm compute cost

---

# 9. License Plate Detection (Phase 2)

Sau khi detect vehicle (Phase 1):
- Polygon/Zone được vẽ trực tiếp trên Web đã build sẵn.
- AI Engine subscribe topic zone qua MQTT để nhận danh sách điểm polygon theo `camera_code`.
- Chỉ giữ xe có tâm bbox nằm trong polygon zone đang active.
- Tracking các xe hợp lệ để gán `track_id`, giữ định danh xe qua nhiều frame và tránh OCR lặp liên tục.
- Crop vùng xe theo `track_id` để làm **Vehicle ROI**.
- Chỉ thực hiện detect biển số xe bên trong **Vehicle ROI**.
- Sau khi detect biển số, crop biển số và đưa qua PaddleOCR để nhận diện ký tự.
- **Yêu cầu quan trọng:** Bbox của phương tiện và bbox của biển số xe phải đi cùng nhau. Việc detect biển số trong crop ROI của xe đảm bảo biển số được gắn đúng xe, tránh nhầm giữa nhiều xe đứng gần nhau.

Pipeline:

```text
Vehicle Detection
    ↓
Polygon Filter by Web Zone
    ↓
Vehicle Tracking
    ↓
Crop Vehicle ROI by track_id
    ↓
Plate Detection (Phase 2 - Nested)
    ↓
Crop Plate
    ↓
OCR Plate Text
```

Điều này giúp:
- tăng tốc inference
- giảm false positive
- tối ưu GPU usage
- Đảm bảo tính liên kết chặt chẽ (gần nhau/đi cùng nhau) giữa xe và biển số xe

---

# 10. PaddleOCR Recognition

Sau khi detect biển số:

```text
Crop Plate
    ↓
Resize
    ↓
Preprocess
    ↓
PaddleOCR
```

---

## OCR Configuration

Cấu hình realtime:

```python
PaddleOCR(
    det=False,
    rec=True,
    cls=False
)
```

Lý do:
- YOLO đã detect plate
- chỉ cần recognition
- giảm latency

---

# 11. Tracking System

Sử dụng:
- ByteTrack
- DeepSORT

để:
- tracking object
- tránh OCR lặp
- tăng độ chính xác

---

# 12. Regex Validation

Sau OCR:
- validate biển số Việt Nam
- sửa ký tự sai

Ví dụ:

```text
O → 0
I → 1
S → 5
```

Regex demo:

```python
r"\d{2}[A-Z]\d?-?\d{4,5}"
```

Regex trên chỉ là mẫu đơn giản để kiểm thử ban đầu. Khi chạy thực tế cần mở rộng để xử lý biển 2 dòng, dấu chấm/dấu gạch, xe máy, biển vàng/trắng và lỗi OCR thường gặp.

---

# 13. Hệ Thống Đẩy Bbox & Giao Tiếp MQTT

Hệ thống giao tiếp và đồng bộ trạng thái thời gian thực thông qua MQTT Broker.

## 13.1. Lấy Danh Sách Camera (Camera Discovery)
- **Topic**: `smart_vms/cameras/company/{company_id}` (Ví dụ: `smart_vms/cameras/company/10` hoặc `/company/21`)
- **Cách xử lý**: Lọc danh sách các camera có trạng thái `ONLINE` và có phân hệ xử lý trùng với cấu hình AI module chạy tại local.
- **LƯU Ý QUAN TRỌNG:** Khi cấu hình và chạy chương trình, bạn phải chọn một `camera_code` tránh các camera mà người khác trong hệ thống đang sử dụng để tránh trùng lặp hoặc xung đột bản tin.
- Nên có cơ chế ghi nhận camera đang được AI local sử dụng, ví dụ cấu hình local cố định, heartbeat/status topic hoặc quy ước team để tránh nhiều tiến trình publish chung một `camera_code`.

## 13.2. Nhận Polygon/Zone Từ Web & Lọc Vùng Detect
- **Topic**: `smart_vms/cameras/{camera_code}/zones` (Để lắng nghe tất cả, dùng wildcard `smart_vms/cameras/+/zones`)
- **Nguồn dữ liệu**: Polygon/Zone được người dùng vẽ trực tiếp trên Web đã build sẵn. AI Engine không cần vẽ polygon, chỉ subscribe MQTT để nhận vùng đã cấu hình.
- **Cách xử lý**: Đọc danh sách các đỉnh đa giác `points` (mỗi điểm dạng `{"x": float, "y": float}`). Thực hiện thuật toán kiểm tra điểm trong đa giác (Point-in-Polygon). Chỉ xử lý xe nếu tâm bbox của xe nằm trong polygon đang active. Sau đó tracking xe hợp lệ để tạo `track_id`, crop vùng xe theo `track_id`, detect biển số trong crop đó, crop biển số và đưa qua OCR.
- **File mẫu tương tác MQTT**: `mock_bbox_publisher.py` đã có ví dụ load camera, subscribe zones topic, parse polygon points và publish bbox lên `smart_vms/ai/bbox/{camera_code}`.

## 13.3. Đẩy Bbox Lên Web (MQTT BBox Publish)
- **Topic**: `smart_vms/ai/bbox/{camera_code}`
- **Payload Format (JSON)**:
```json
{
  "camera_code": "CAM_01_TEST",
  "ai_module": "VEHICLE",
  "ai_modules": ["VEHICLE"],
  "timestamp": 1785002934.12,
  "detections": [
    {
      "id": "car_track_12",
      "cls": "car",
      "class": "car",
      "label": "car",
      "class_id": 0,
      "confidence": 0.95,
      "bbox": [0.15, 0.20, 0.45, 0.50],
      "color": "#00ff00",
      "plate": {
        "id": "plate_track_12",
        "bbox": [0.25, 0.42, 0.34, 0.48],
        "text": "30A12345",
        "confidence": 0.88
      }
    }
  ]
}
```
*Tọa độ `bbox` sử dụng tỉ lệ chuẩn hóa từ `0.0` đến `1.0` so với kích thước khung hình.*

---

# 14. Khả Năng Mở Rộng Multi-camera

Hiện tại:
```text
Ưu tiên xử lý 1 camera realtime ổn định
```

Tuy nhiên hệ thống đã hỗ trợ mở rộng:

```text
Camera 1 → Pipeline 1
Camera 2 → Pipeline 2
Camera 3 → Pipeline 3
```

---

## Scaling Strategy

Mỗi camera có:
- decode thread riêng
- AI queue riêng
- tracking riêng

Shared:
- GPU inference
- OCR worker pool

---

# 15. GPU Configuration

## Server

| Thành phần | Thông tin |
|---|---|
| OS | Ubuntu 22.04.5 LTS |
| Kernel | GNU/Linux 6.8.0-86-generic x86_64 |
| GPU | NVIDIA RTX A400 |
| VRAM | 4GB |
| CUDA Runtime hiển thị bởi driver | 13.0 |
| Driver | 580.126.09 |
| Web console | https://aiot:9090/ |

Lưu ý compatibility:
- CUDA `13.0` là version runtime hiển thị bởi NVIDIA driver, không đồng nghĩa PaddlePaddle/YOLO đang dùng đúng CUDA toolkit version này.
- PaddlePaddle GPU cần kiểm tra wheel tương thích với CUDA thực tế trong môi trường Python/Conda trước khi cài hoặc chạy OCR.
- GPU đang có tiến trình Python khác sử dụng VRAM, nên cần kiểm tra tải GPU trước khi benchmark latency.

---

## NVIDIA-SMI

```bash
+-----------------------------------------------------------------------------------------+
| NVIDIA-SMI 580.126.09             Driver Version: 580.126.09     CUDA Version: 13.0     |
+-----------------------------------------+------------------------+----------------------+
| GPU  Name                 Persistence-M | Bus-Id          Disp.A | Volatile Uncorr. ECC |
|=========================================+========================+======================|
|   0  NVIDIA RTX A400                On  |   00000000:01:00.0 Off |                  N/A |
| 57%   81C    P0            N/A  /   50W |    1545MiB /   4094MiB |     76%      Default |
+-----------------------------------------+------------------------+----------------------+
```

---

# 16. Độ Trễ Hệ Thống

| Thành phần | Delay |
|---|---|
| Decode | 20–80ms |
| Vehicle Detection (Phase 1) | 5–20ms |
| Plate Detection (Phase 2) | 5–15ms |
| OCR (Phase 3 - Optional) | 5–20ms |
| MQTT Bbox Publish | 1–5ms |

---

## Tổng Delay

```text
40ms – 140ms
```

Đây là mục tiêu/ước lượng trong điều kiện tối ưu. Khi RTSP network jitter, OCR bật, GPU đang tải cao hoặc nhiều tiến trình Python dùng VRAM, độ trễ thực tế có thể cao hơn.

---

# 17. Tối Ưu Hệ Thống

## AI Optimization
- YOLO11n
- TensorRT
- ONNX Runtime

## Transmission Optimization (MQTT Bbox)
- Tách luồng gửi MQTT sang thread riêng (Thread 4) để tránh block luồng suy diễn AI.
- Sử dụng cấu hình QoS=0 để tối ưu hóa độ trễ truyền dữ liệu qua mạng.
- Sử dụng tọa độ chuẩn hóa gọn nhẹ để tiết kiệm băng thông tối đa.

## OCR Optimization (Phase 3)
- recognition only
- resize plate
- grayscale preprocessing

---

# 18. Kết Luận

Hệ thống AI realtime làm quen với MQTT hiện tại hỗ trợ:
- vehicle detection (Phase 1)
- license plate detection (Phase 2)
- OCR realtime (Phase 3 - Optional)
- MQTT camera discovery & zone polygons loading
- MQTT realtime BBox publishing to web
- low latency processing & multi-thread architecture
- scalable multi-camera design (chạy tối ưu với 1 camera và có cơ chế tránh camera_code bị trùng lắp)

với khả năng xử lý realtime ổn định trên GPU NVIDIA RTX A400 4GB.

---

# 19. Cấu Trúc Thư Mục & File Dự Án (Project Directory Structure)

Dưới đây là sơ đồ thiết kế cấu trúc thư mục của một dự án AI hoàn chỉnh trong thực tế giúp phân tách rõ ràng các mô-đun xử lý (Decoupled & Modular Design):

## 19.1. Cấu Trúc Mã Nguồn Dự Án Đề Xuất (AI Engine Module)

```text
ai_recognition_project/
├── config/
│   └── settings.yaml             # Cấu hình MQTT Broker, các topic, đường dẫn models và cấu hình camera chạy thử nghiệm
├── models/
│   ├── vehicle_detect.pt         # Trọng số YOLOv11/YOLOv8 phát hiện phương tiện (Phase 1)
│   ├── plate_detect.pt           # Trọng số YOLOv11/YOLOv8 phát hiện biển số xe (Phase 2)
│   └── paddleocr/                # Thư mục chứa model cấu trúc và trọng số OCR của PaddleOCR (Phase 3)
├── src/
│   ├── __init__.py
│   ├── pipeline.py               # Module quản lý luồng dữ liệu chính: điều phối queues, khởi chạy và tắt các threads
│   ├── config_loader.py          # Hỗ trợ đọc các thông số cấu hình từ file yaml hoặc tham số dòng lệnh
│   ├── mqtt/
│   │   ├── __init__.py
│   │   ├── client.py             # Quản lý kết nối, bắt tay, reconnect tới MQTT Broker
│   │   ├── discovery.py          # Lắng nghe danh sách camera ONLINE để lấy luồng RTSP động
│   │   ├── zones.py              # Xử lý cập nhật polygon hoạt động và thuật toán lọc Point-in-Polygon
│   │   └── publisher.py          # Đóng gói và đẩy dữ liệu bbox định dạng JSON lên Broker thời gian thực (Thread 4)
│   ├── video/
│   │   ├── __init__.py
│   │   └── gstreamer_decode.py   # Thread 1 - Giải mã luồng RTSP sử dụng GStreamer pipeline đẩy vào Frame Queue
│   ├── ai/
│   │   ├── __init__.py
│   │   ├── vehicle_detector.py   # Thread 2 - Phase 1: YOLO nhận dạng xe
│   │   ├── plate_detector.py     # Thread 2 - Phase 2: YOLO nhận dạng biển số (giới hạn tìm kiếm trong Vehicle ROI)
│   │   ├── tracker.py            # Liên kết tracking các frame liên tiếp (ByteTrack/DeepSORT)
│   │   └── ocr_engine.py         # Thread 3 - Phase 3 (Optional): Trích xuất ký tự biển số
│   └── utils/
│       ├── __init__.py
│       ├── geometry.py           # Tiện ích toán học hỗ trợ vẽ và tính toán diện tích/vị trí polygon
│       └── validation.py         # Regex kiểm tra và sửa lỗi ký tự nhận dạng biển số xe Việt Nam
├── main.py                       # Điểm khởi chạy (Entrypoint) chính của toàn bộ chương trình AI
└── requirements.txt              # Danh sách thư viện Python (paho-mqtt, opencv-python, ultralytics, paddlepaddle, etc.)
```

---

## 19.2. Các File Nghiên Cứu Hiện Tại Trong Workspace

Mã nguồn workspace hiện tại chứa các file phục vụ nghiên cứu, tìm hiểu API Web và kiểm thử luồng truyền nhận MQTT:

```text
tuan_3v1/
├── docs.md                       # Tài liệu hướng dẫn hệ thống & thông số kỹ thuật (file này)
├── mock_bbox_publisher.py         # Script giả lập AI Engine: tự động quét camera, đăng ký zones và bắn bbox thử nghiệm
├── get_cameras.py                 # Client MQTT để subscribe & in ra danh sách Camera để kiểm tra kết nối Broker
├── get_cameras_web.py             # Client HTTP kết nối Web API để lấy trực tiếp danh sách camera hỗ trợ so sánh dữ liệu
└── search_q_definition.py         # Công cụ quét tệp tin JS bundle của Web Admin phục vụ việc dò tìm endpoints
```