# assets

## 프로필 배경 이미지

`/프로필` 카드의 배경으로 쓸 이미지를 이 폴더에 **`profile_bg.png`** 라는 이름으로
넣어 주세요.

- 권장 크기: **1000 × 420** (다른 비율이어도 가운데를 기준으로 잘라서 채웁니다)
- 다른 파일명을 쓰려면 `.env` 의 `PROFILE_BACKGROUND` 를 바꾸면 됩니다.
- 파일이 없으면 봇이 네이비→골드 그라데이션 배경을 직접 그려서 대신 씁니다.

카드 위에는 반투명한 남색 패널이 깔리므로, 배경이 밝거나 복잡해도 글자는 읽힙니다.
테두리·강조색은 골드(`#C8AA6E`)와 청록(`#0AC8B9`)으로 맞춰져 있습니다.
바꾸고 싶다면 `utils/card.py` 위쪽의 팔레트 상수를 수정하세요.

## 한글 폰트

카드에 한글을 그리려면 한글 폰트가 필요합니다. 아래 중 **하나**를 `assets/fonts/` 에
넣어 주세요. (없으면 시스템 폰트를 찾아보고, 그것도 없으면 글자가 깨집니다.)

```
assets/fonts/Pretendard-Bold.ttf
assets/fonts/Pretendard-Regular.ttf
```

또는 `NanumGothicBold.ttf` / `NanumGothic.ttf`,
`NotoSansKR-Bold.ttf` / `NotoSansKR-Regular.ttf` 도 자동으로 인식합니다.

리눅스 서버라면 아래 한 줄로 설치해도 됩니다.

```bash
sudo apt install fonts-nanum
```
