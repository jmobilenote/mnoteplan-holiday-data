# mNotePlan 대한민국 공휴일 데이터

공공데이터포털 API에서 대한민국 공휴일을 받아 정적 JSON으로 배포합니다. GitHub Actions가 매일 03:17 한국시간에 갱신하고, GitHub Pages가 JSON을 제공합니다.

## 최초 설정

1. 이 저장소의 **Settings → Secrets and variables → Actions → New repository secret**에서 `KASI_SERVICE_KEY`를 만들고 공공데이터포털 일반 인증키(Decoding)를 붙여 넣습니다. 키를 이 저장소에 일반 파일로 올리지 마세요.
2. **Settings → Pages → Build and deployment → Source**를 **GitHub Actions**로 설정합니다.
3. 먼저 **Actions → Publish holiday data to GitHub Pages → Run workflow**를 실행해 현재 JSON을 게시합니다.
4. Secret 저장 후 **Actions → Update and publish Korean holidays → Run workflow**를 실행해 실제 API 갱신까지 확인합니다. 이후 매일 자동 실행됩니다.

Pages 주소: https://jmobilenote.github.io/mnoteplan-holiday-data/

- Manifest: https://jmobilenote.github.io/mnoteplan-holiday-data/manifest.json
- 현재 연도별 JSON 경로는 manifest의 `countries.kr` 아래에서 확인합니다.

앱은 기본 주소 뒤에 `manifest.json`을 요청하고, manifest에 있는 연도별 `path`를 따라갑니다. 스키마 버전은 1입니다. 연도 데이터는 변경 때마다 새 파일명으로 게시하며 SHA-256을 제공합니다. 앱에서 해시를 검사한 뒤에만 로컬 캐시를 교체해야 합니다.

API 호출은 전체 사용자 기기가 아니라 이 저장소의 Actions에서만 수행합니다. API 조회에 실패하면 기존 JSON은 유지됩니다. 임시공휴일 반영은 제공기관 데이터 갱신과 다음 정상 실행 이후입니다.
