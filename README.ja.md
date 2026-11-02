# Bad Apple!! / 1フレーム = Unicode 1文字

[English](README.md) | **日本語**

**ブラウザ版：https://unicode-frames.ikutotest.xyz/** （任意の動画で、インストール不要）

Bad Apple!! の各フレームに最も近い **実在のUnicode 1文字** を、約41万の字形候補から全探索で選び、動画にするツールです。中央に選ばれた文字、右上に元映像、下部に `U+XXXX 文字名` とUnicode上の位置を表示します。音声は元動画から取り込みます。

- 4列×3行の12文字版: [docs/GRID-4X3.md](docs/GRID-4X3.md)
- 8列×6行の48文字版: [docs/GRID-8X6.md](docs/GRID-8X6.md)
- ブラウザ版（任意の動画・任意の文字数）: [web/](web/) — 下の「ブラウザ版」を参照

## ブラウザ版

[web/](web/) は、任意の動画から同じスタイルの動画を作る静的サイトです。Cloudflareで公開しています。動画はアップロードせず、解析も書き出しもブラウザ内で行います。UIは英語（既定）と日本語です。

- 画面に使う文字数を列×行で指定します（1×1〜64×48）。各マスを独立に照合します。
- 使うUnicodeブロックを選べます。プリセット（ASCII、ラテン文字、かな、漢字、罫線・ブロック図形、点字、絵文字）と、名前・コードポイントでの絞り込みがあります。選んだブロックはブラウザに保存されます。空白を候補に含めるかも切り替えられ、こちらは再解析なしで反映されます。
- 設定画面で、選んだ位置の1フレームをその設定で照合したプレビューを表示します。文字数やブロックを変えるとすぐに更新されます。
- 元フレームと使用した字形（コードポイント、ブロック、文字名、書体、数直線）の表示を切り替えられます。
- 書き出しはWebCodecsによるMP4（H.264 / H.265 / VP9 / AV1）です。音声は付けるか消すかを選べます。付ける場合、MP4に入る形式ならそのままコピーし、それ以外はAACかOpusに再エンコードします。文字列も `.txt`（1フレーム1行）で保存できます。

照合はCLIと同じ410,510字形が対象です。特徴量はCLIと同じ32×24・ぼかし0.65で、PillowのLanczos縮小とGaussianBlurの丸めまで移植しています（Pillowと全画素一致することをテストで確認）。配信できるサイズにするため、768次元の特徴量をPCAで128次元に圧縮し、全次元共通のスケールでint8にしています（52MB、分散の91.3%を保持）。WebGPUのint8内積で全候補を総当たりし、同距離なら辞書の先頭の候補を選びます。WebGPUがない環境ではWeb Workerで同じ計算をします。Bad Appleの1文字版（6,955フレーム）では、CLIの768次元全探索と同じ文字を選んだフレームが81.0%でした。異なるフレームも、選んだ文字の距離は最適値と平均0.6%しか違いません。

ブロックの選択は候補ごとのコードポイント（`data/codepoints.bin`）から作るビットマスクで、GPUの全探索でマスク外の候補を飛ばします。字形の輪郭は候補ごとに `data/glyphs-*.bin` に格納します。Unifontはドット形状をそのままビットマップで持ちます。

サイトを開くと、まず必要なデータの量（約260MB）とブラウザの空き容量を表示し、許可を求めます。許可するとすべてのデータをダウンロードしてブラウザ（Cache Storage）に保存し、次回からは確認なしで保存済みのデータを使います。保存したデータはページ下部のリンクから削除できます。字形は保存済みのファイルから必要な部分だけを読み出すため、配信側にHTTP Rangeへの対応は要りません。

### データの生成と公開

`fetch` と `build` で辞書を作った後、ブラウザ用のファイルを書き出します（約20秒、出力は約255MB）。

```powershell
.venv\Scripts\python -m badapple_unicode web-export --dictionary cache/full-dictionary --output web/data
```

ローカルで確認するには、`web/` を静的サーバーで配信します。

```powershell
node tools/serve-web.mjs 8000
```

`web/data/` は生成物なのでmainブランチには含めません。公開はCloudflareのWorker（静的アセットのみ）で行います。静的ファイルへのリクエストは無料で、回数の制限もありません。指定したドメインだけで配信し、`workers.dev` やプレビュー用のURLは無効にします。ドメインは同じCloudflareアカウントのゾーンである必要があります。初回は `npx wrangler@4.151.2 login` でログインします。字形ファイルはCloudflareの上限（1ファイル25 MiB）に収まるよう20MBごとに分けています。

```powershell
.\tools\publish-cloudflare.ps1 -Domain unicode-frames.ikutotest.xyz
```

Cloudflare Accessで既定ですべてのホストを保護している場合は、公開するホスト名に「Bypass / Everyone」のポリシーを付けたアプリケーションが必要です。

対応ブラウザ：WebCodecsが必要です（Chrome / Edge、Safari、Firefoxの最近の版）。WebGPUがあると照合が大幅に速くなります。

## 必要なもの

- Python 3.11以上
- PATHに登録されたFFmpeg / FFprobe
- （任意）NVIDIA GPU：CUDA対応PyTorch、またはグリッド版用のCUDAライブラリ（CUDA Toolkit + MSVC）
- フォント・Unicodeデータ・元動画の取得に約1GBのディスクとネットワーク接続

## 実行（Windows）

```powershell
.\setup.ps1
.\run.ps1
```

`run.ps1` はフォントと元動画の取得（`assets/`）、辞書の生成（`cache/`）、照合と動画出力（`output/`）を順に行います。2回目以降は辞書を再利用します。

別の動画も指定できます。

```powershell
.\run.ps1 -InputVideo 'D:\videos\badapple.mp4' -OutputDirectory output-custom
```

## 実行（macOS / Linux / 手動）

```bash
python -m venv .venv
.venv/bin/python -m pip install -e '.[cpu,test]'
.venv/bin/python -m badapple_unicode fetch
.venv/bin/python -m badapple_unicode build --dictionary cache/full-dictionary
.venv/bin/python -m badapple_unicode run --dictionary cache/full-dictionary
.venv/bin/python -m badapple_unicode play output/characters.txt
```

動作確認したバージョンは `requirements-lock.txt` にあります。

## 辞書とフォールバック

- [公式Noto配布リポジトリ](https://github.com/notofonts/notofonts.github.io)の全ファミリーについて、Full版のRegularを取得します。Full版がないファミリーではUnhinted版、Regularがなければ利用可能なインスタンスを採用します。Sans / Serif / Mono / 歴史的文字・記号を含みます。
- [Noto CJK](https://github.com/notofonts/noto-cjk)のSans / Serif、全5地域と、[Noto Emoji](https://github.com/google/fonts/tree/main/ofl/notoemoji)の単色フォントも含めます。
- [GNU Unifont 17.0.05](https://ftp.gnu.org/gnu/unifont/unifont-17.0.05/)の基本面・補助面・日本語・繁体字版を加えます。
- Noto / Unifontでは拡張漢字に大きな不足があるため、[Jigmo 2025-09-12](https://kamichikoichi.github.io/jigmo/)の3分割フォントも追加します。`fetch --no-jigmo` で外せます。
- **全ウェイト・全幅・斜体も使う場合は `fetch --all-styles`、または `.\run.ps1 -AllStyles`**。通常は全ファミリーの標準書体を使います。可変フォントの連続する軸位置の全列挙は行わず、デフォルトインスタンスを描画します。

Unicode 17の割り当て済みスカラー値を対象にします。制御・書式・私用・サロゲート・未割り当て・空白・置換文字を除きます。結合文字は、単独でインクが描ける場合に含みます。書体ごとのcmapとグリフIDを検査し、空描画と `.notdef` と同一の豆腐を取り除きます。本物の四角記号を形だけで一括除外しません。

既定では各フォントに存在する全対象文字を候補にします。他フォントの同じ文字も見た目が違えば別候補です。`build --fallback-only` は、文字ごとに最初に描画できたフォントだけを使う高速オプションです。

128×96の共通キャンバスに、インク領域を切り出したグリフを描きます。伸張版と比率維持版の両キャンバスが画素単位で一致する候補をSHA-256で重複排除します。32×24への縮小やぼかしで偶然似た候補は重複扱いしません。別名・別コードポイント・フォントの対応はSQLiteの `aliases` に保持します。

**フォント集合には未収録文字があります。全Unicodeを網羅したと偽らないため、件数は `manifest.json`、各文字は `missing.tsv` に出力します。** 空の結合文字・異体字セレクタなどもこの一覧に現れます。フォントを追加して別名の辞書をビルドすると補えます。

```powershell
.venv\Scripts\python -m badapple_unicode build --fonts assets/fonts extra-fonts --dictionary cache/expanded
```

## 正規化と比較

`stretch` は文字のインク領域を4:3の枠いっぱいに伸張し、`fit` は縦横比を保って中央配置します。元動画は両方式で同じ4:3キャンバスへ縮小します。両方を32×24にし、標準偏差0.65pxのGaussianぼかしを適用します。前景は白、背景は黒で固定します。フレーム単位の反転・鏡像・文字列・合成文字は使いません。

既定の `--normalization auto` は動画全体から等間隔の128フレームを選び、両方式で全候補を検索し、平均距離の小さい方式を採用します。`normalization-comparison.png` と `comparison.json` で選択の根拠を確認できます。`--normalization stretch` / `fit` で固定もできます。

`--metric l2` は0〜1の画素値に対する平均二乗誤差です。`--metric iou` はぼかしたマスクの `1 - sum(min(A,B))/sum(max(A,B))` です。両方が空なら距離0と定義します。

全探索は `||q||² + ||g||² - 2 qgᵀ` の行列積で行い、メモリを抑えるためチャンク分割します。近似探索や時間方向の平滑化は入れず、各フレームの最近傍を独立に選びます。

| バックエンド | 特徴 |
| --- | --- |
| `auto` | CUDA対応PyTorch → FAISS（L2）→ NumPyの順で選択 |
| `faiss` | CPUの `IndexFlatL2` による全探索。学習不要 |
| `numpy` | 追加の検索ライブラリ不要。L2 / IoU両方に対応 |
| `cuda` | CUDA対応PyTorchが必要。float32、TF32無効。L2 / IoU両方に対応 |

FAISSはオプション依存の `.[cpu]`、PyTorchは `.[gpu]` です。CUDA版のインストール方法は[PyTorch公式](https://pytorch.org/get-started/locally/)を参照してください。IoUのCPU全探索はL2より重いため、大規模処理ではCUDAを推奨します。

```powershell
.venv\Scripts\python -m badapple_unicode run --dictionary cache/full-dictionary --backend cuda
.venv\Scripts\python -m badapple_unicode run --dictionary cache/full-dictionary --metric iou --backend numpy
```

元動画の分数fps（今回の素材では30000/1001）を維持します。VFR素材は同fpsのCFRにサンプリングします。`--fps 30` や `--max-frames 300` で変更できます。辞書とフレームは再利用可能なキャッシュに保存します。キャッシュ生成中に停止した `.building` フォルダーは使用しません。

## 高解像度の描画・再出力

照合用の128pxグリフと、本編で表示するグリフの描画を分離しています。本編では元のTTF / OTFから輪郭を取得し、縦横の変形をベクトル座標に適用してから、表示サイズの2倍で描画して縮小します。小さなラスタ画像を引き伸ばすことによるぼやけを防ぎます。細い横線なども、伸張前のピクセル数に制約されません。照合用の32×24特徴量や選ばれる文字はこの変更で再計算しません。

既存の `frames.jsonl` を再利用して、2560×1920の高精細版を作るコマンドです。辞書生成・全探索を省略できます。

```powershell
.venv\Scripts\python -m badapple_unicode render --results output --encoder h264_nvenc
```

出力は `output/badapple-unicode-hq.mp4` と、その描画条件を記録した `.render.json` です。NVIDIAエンコーダーがない場合は `--encoder libx264`、従来と同じ動画サイズなら `--size 1280x960` を指定できます。新規の `run` も既定で同じ輪郭描画を使います。`--supersample 1` から `4` まで指定できます。

文字と音声だけの版は次のコマンドで作成します。選ばれた1文字を4:3の画面いっぱいに描き、元映像の小窓・タイトル・字幕・数直線を表示しません。文字選択とフレームレートは既存結果を再利用します。

```powershell
.venv\Scripts\python -m badapple_unicode render --results output --layout glyph-only --encoder h264_nvenc
```

出力は `output/badapple-unicode-glyph-only.mp4`（2560×1920・音声付き）です。

元の書体・フォントコレクションのフェイス番号を維持します。SHA-256が辞書作成時と違うフォントは拒否し、意図せず別の書体へ変わることを防ぎます。表示用の高解像度キャッシュは `cache/full-dictionary/display-cache/` に保存します。比較用に旧方式を再現する場合だけ `--glyph-renderer cached` を指定します。

Unifontの階段状の輪郭は、元フォントが持つドット形状です。輪郭を直接描いても、別書体のような滑らかな曲線には変わりません。

## 字幕の固定とUnicode上の位置

下部の字幕は等幅フォントを使い、コードポイント・文字名・書体名・正規化方式・フレーム番号を固定位置に表示します。行はベースラインで揃え、書体名が長くなっても後続の項目が動かないようにしています。フレーム番号は桁数を固定します。

コードポイントの横にUnicodeブロック名とその範囲を表示します。データはUnicode 17公式の `Blocks.txt` をパッケージに収録し、利用許諾文を `badapple_unicode/data/UNICODE-LICENSE.txt` に保持しています。

- `UNICODE` の数直線はU+0000からU+10FFFFまでの全コードポイントを表します。所属ブロックを青緑色、選ばれた文字の位置を黄色で示します。短い目盛りはUnicodeの各面の開始位置です。
- `BLOCK` の数直線は現在のブロックの先頭から末尾までを拡大したものです。Unicode全体では左端に密集する文字も、ブロック内の位置を読めます。

両方ともコードポイントの数値に比例した位置を使います。目盛りと端点のラベルは固定し、文字が変わるとマーカーだけが横に移動します。中央の文字を大きく保ちつつ、下部に数直線2本が収まるよう配置しています。

```powershell
.venv\Scripts\python -m badapple_unicode render --results output --output output/badapple-unicode-hq-position.mp4 --encoder h264_nvenc
```

## 出力

| ファイル | 内容 |
| --- | --- |
| `badapple-unicode.mp4` | 1280×960、文字＋元映像＋文字名＋音声 |
| `badapple-unicode.poster.png` | 動画の静止画 |
| `badapple-unicode-hq.mp4` | 元の文字選択を再利用した2560×1920の輪郭描画版 |
| `badapple-unicode-hq-position.mp4` | 字幕固定・Unicodeブロック名・数直線2本を加えた高精細版 |
| `badapple-unicode-glyph-only.mp4` | 画面いっぱいの1文字と音声だけの版 |
| `characters.txt` | UTF-8、BOM・改行なし、1フレームにつき1スカラー値 |
| `characters-collapsed.txt` | 隣り合う同じ文字を1文字にまとめた、改行なしのUTF-8文字列 |
| `timing.json` | 分数fpsとフレーム数 |
| `characters.zip` | 文字列・時刻情報・外部ライブラリ不要の再生スクリプト |
| `frames.jsonl` | 各フレームの文字・名前・フォント・距離・時刻 |
| `characters.srt` | 同じ選択が続く区間をまとめた字幕 |
| `normalization-comparison.png` | 元フレーム・伸張・比率維持の比較 |
| `comparison.json` / `summary.json` | 正規化選択・検索結果・処理時間のレポート |

`characters.txt` と `timing.json` を同じフォルダーに置くと `play` で再生できます。文字列をUnicode正規化するとコードポイント数が変わることがあるため、そのまま扱ってください。端末での見え方は端末のフォントフォールバックとセル寸法に依存します。結合文字や補助面の文字を表示できない端末もあります。動画は選ばれた元のフォントから描画するので、再生環境のフォントに左右されません。

配布用の `characters.zip` は展開後に `python play_characters.py` だけで再生できます。Python以外の依存関係はありません。

`characters-collapsed.txt` もZIPに含めます。例えば `AAABBAA` は `ABA` になります。離れた場所での再出現は残し、Unicode正規化は行いません。文字数がフレーム数と異なるため、この短縮版には元のfps情報を適用しません。

白紙に相当する文字を候補から除いているので、真っ黒なフレームにも何らかの文字が選ばれます。

## 参考：作者環境での実行結果

244フォント、155,230コードポイント、見た目の異なる410,510候補。6,955フレームをFAISS CPUで全探索し、H.264 NVENCで1280×960の音声付き動画を書き出しました。照合と動画出力は合計約122秒、辞書生成は約440秒でした。選ばれた文字は902種類です。

128サンプルの平均MSEは伸張0.061099、比率維持0.083048で、伸張の方が約26%小さくなりました。

未収録・単独でインクを描けない文字は4,381字です。内訳はエジプト象形文字3,995、異体字セレクタ240、西夏文字の部品115、西夏文字30、空のハングルフィラー1です。

## テスト

```powershell
.venv\Scripts\python -m pytest -q
```

小さな人工フォントを使い、cmap未収録・豆腐・空白の除外、補助面、見た目の重複、後続フォントへのフォールバックを確認します。検索は直接計算した総当たり距離と照合します。FFmpegが利用できる環境では、分数fps、全フレーム、音声、文字列とメタデータの一致も確認します。輪郭描画、字幕レイアウトの固定、グリッド版の配置もテストしています。

## ライセンス

このリポジトリのコードは [MIT License](LICENSE) です。以下のフォント・動画・Unicodeデータはそれぞれのライセンスに従います。

## 素材とライセンス

このリポジトリにはフォント・元動画を含めません。`fetch` が実行時に取得し、取得元URL・Gitリビジョン・SHA-256を `assets/manifest.json`、動画の取得元を `assets/source.json` に記録します。各フォントのライセンス文書は `assets/licenses/` に保存されます。

- 元動画：[bad-apple-lab/Bad-Apple](https://github.com/bad-apple-lab/Bad-Apple) の公開コピー。元映像・音源の権利はそれぞれの権利者に帰属します。
- フォント：Noto（SIL OFL 1.1）、GNU Unifont（GPL with font embedding exception / OFL）、Jigmo（CC0 1.0）
- Unicodeデータ：`badapple_unicode/data/Blocks-17.0.0.txt` は Unicode, Inc. の [Unicode License v3](badapple_unicode/data/UNICODE-LICENSE.txt) に従います。
