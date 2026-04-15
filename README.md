# TRANSVID.EXE

Windows용 드래그앤드롭 영상 변환기입니다.  
영상 파일이나 폴더를 창에 끌어다 놓으면 `ffprobe`로 분석한 뒤 `ffmpeg`로 HEVC 변환을 수행합니다.

현재 기준으로는 `Windows` 환경을 우선 대상으로 만들었고, `NVIDIA / Intel / AMD / CPU` 순으로 사용 가능한 인코더를 자동 감지합니다.

## 장점

- 하드웨어 자동 감지
  - `NVIDIA / Intel / AMD / CPU` 중 현재 PC에서 실제로 사용 가능한 HEVC 인코더를 자동으로 골라줍니다.
  - 데스크탑과 노트북을 번갈아 써도 코덱 설정을 다시 만질 필요가 적습니다.

- 설정이 실전형으로 잡혀 있음
  - 기본값이 `속도 / 용량 / 화질` 균형 쪽으로 맞춰져 있어서 바로 드래그앤드롭해서 돌리기 좋습니다.
  - `CQ`, `출력 FPS`, `동시 작업 수`만 바꿔도 대부분의 사용 시나리오를 커버합니다.

- 예상 속도 자동 보정
  - 인코더별 기본 속도로 시작하고, 실제 완료 속도를 반영해서 다음 예상치를 자동으로 맞춥니다.
  - 기계마다 성능이 달라도 시간이 지날수록 예측이 현실에 가까워집니다.

- 대량 작업에 맞는 배치 흐름
  - 폴더째 드롭해서 한 번에 큐를 만들 수 있고, 이미 HEVC인 파일이나 기존 출력 파일은 자동으로 건너뜁니다.
  - 완료 후 원본 삭제 옵션까지 있어서 저장공간 정리 흐름을 같이 가져갈 수 있습니다.

- 윈도우 로컬 툴답게 단순함
  - 브라우저나 서버 없이 바로 실행하는 구조라서 영상 여러 개를 빠르게 처리하기 좋습니다.
  - GitHub Actions로 Windows 빌드도 자동화돼 있어 exe 배포 흐름까지 연결하기 쉽습니다.

## 주요 기능

- 파일/폴더 드래그앤드롭으로 큐 추가
- `hevc_nvenc -> hevc_qsv -> hevc_amf -> libx265` 자동 감지
- `CQ`, 출력 FPS, 동시 작업 수 조절
- 예상 처리 속도 표시
- 실측 속도로 `예상 기준 속도` 자동 보정
- 이미 HEVC인 파일 건너뛰기
- 출력 파일이 있으면 건너뛰기
- 변환 완료 후 원본 삭제 옵션
- Windows exe 빌드 및 GitHub Actions 자동 빌드

## 요구 사항

- Windows 10/11 권장
- Python `3.11+`
- `ffmpeg`, `ffprobe`가 PATH에 있어야 함

주의:

- 현재 앱은 `ffmpeg`를 직접 포함해서 배포하지 않습니다.
- 사용자가 시스템에 설치한 `ffmpeg` / `ffprobe`를 사용합니다.

## 빠른 실행

### 1. `uv` 사용

```bash
uv sync
uv run video-drop-converter
```

또는

```bash
uv run python app.py
```

### 2. `pip` 사용

```bash
python -m pip install -r requirements.txt
python app.py
```

## 사용 방법

1. 앱을 실행합니다.
2. 영상 파일이나 폴더를 창에 드래그앤드롭합니다.
3. 설정을 확인한 뒤 변환을 시작합니다.
4. 표와 로그에서 진행률, 속도, 완료 여부를 확인합니다.

## 기본 동작

- 비디오 코덱: 자동 감지된 HEVC 인코더 사용
- 오디오: `copy`
- 출력 파일명: `원본파일명.<codec>_<preset>_cq<값>[_fps24].mp4`
- 출력 위치: 원본 파일과 같은 폴더

예:

```text
sample.mp4
-> sample.hevc_nvenc_p1_cq28_fps24.mp4
```

## 하드웨어 감지

앱 시작 시 짧은 테스트 인코딩으로 실제 사용 가능한 인코더를 확인합니다.

우선순위:

1. `hevc_nvenc`
2. `hevc_qsv`
3. `hevc_amf`
4. `libx265`

예상 기준 속도는 인코더별 기본값으로 시작하고, 실제 완료 속도를 반영해 자동 보정됩니다.

## 빌드

PyInstaller 기반 Windows `onedir` 빌드를 제공합니다.

```bash
uv sync --extra build
uv run python scripts/build_windows.py
```

산출물:

- `dist/TRANSVID/TRANSVID.exe`
- `dist/TRANSVID/*` 실행에 필요한 DLL/Qt 파일
- `artifacts/TRANSVID-<version>-windows-x64.zip`

## GitHub Actions

`.github/workflows/build-windows.yml` 이 포함되어 있습니다.

- `push` / `pull_request` / 수동 실행 시 Windows onedir 자동 빌드
- Actions artifact로 실행 폴더와 zip 다운로드 가능
- `v*` 태그 푸시 시 GitHub Release에 zip 자산 자동 업로드

## 손상 파일

일부 MP4는 정상 종료되지 않아 분석이 실패할 수 있습니다.

대표적인 예:

- `moov atom not found`

이 경우는 설정 문제가 아니라 원본 파일 손상 또는 미완성 녹화일 가능성이 큽니다.

## 라이선스

이 저장소의 소스 코드는 [MIT License](LICENSE)로 배포합니다.

다만 의존성은 각자 자체 라이선스를 유지합니다. 특히 배포용 exe를 만들 때는 아래를 따로 확인해야 합니다.
