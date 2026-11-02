# 4列×3行、1フレーム12文字版

元フレームを4列×3行に区切り、各マスに最も近いUnicode文字を独立に選びます。12マスをつなげて元の4:3映像を再構成します。表示は12文字、元映像の小窓、固定位置のフレーム番号、12個のコードポイントに簡素化しています。音声も維持します。

```powershell
.venv\Scripts\python -m badapple_unicode grid --encoder h264_nvenc
```

CPU検索とソフトウェアエンコードを明示する場合：

```powershell
.venv\Scripts\python -m badapple_unicode grid --backend faiss --encoder libx264
```

既存の `cache/full-dictionary` を再利用します。各セルを32×24へ正規化して少しぼかし、全410,510候補から平均二乗誤差が最小のものを選びます。元のフレーム順に並んだ12セルのクエリで完全一致するものだけをまとめて検索し、照合後に元の位置と順序へ戻します。空白を含まない元の辞書を使い、時間方向の平滑化は行いません。

各マスは正方形になります。検索時はセル全体とグリフのインク領域を同じ32×24特徴量へ伸張し、動画では同じインク領域を正方形のマスへ直接ベクトル描画します。128pxのグリフ画像を動画サイズへ拡大する方式には戻していません。

## オプションのCUDA検索

Windowsでは、CUDA ToolkitとVisual Studio（MSVC）があれば専用の小さなライブラリをビルドできます。

```powershell
.\cuda\unicode-grid\build.ps1
.venv\Scripts\python -m badapple_unicode grid --backend native --encoder h264_nvenc
```

ビルド設定はRTX 4090（sm_89）向けです。`auto` はビルド済みの専用ライブラリがあれば使用し、なければ従来の検索バックエンドを選びます。PyTorchの追加インストールは不要です。

0〜255の画素値を128だけ平行移動してint8に格納し、cuBLASの整数行列積とint32の二乗距離で全探索します。`(a-128)-(b-128)=a-b` なので距離は変わらず、元の8bit値を間引く量子化も行いません。同距離なら辞書内の先頭候補を選びます。浮動小数点の桁落ちを使わず、既存の8bit特徴量に対して厳密な最近傍を求めます。

## 出力

`output/grid-4x3/` に保存します。

- `badapple-unicode-4x3.mp4`：2560×1920、音声付きの全編
- `badapple-unicode-4x3.poster.png`：動画の静止画
- `characters.txt`：1フレームにつき12コードポイントを一行に記録。左上から行優先の順序
- `frames.jsonl`：フレームごとの12文字、コードポイント、グリフID、距離、時刻
- `matches.npz`：再出力用の検索結果
- `summary.json` / `timing.json`：fps、フレーム数、照合条件など

短い確認動画は `--max-frames 300 --output output/grid-4x3-preview --size 1280x960` で作れます。照合結果が同じ出力フォルダーにあれば再利用するので、表示サイズやエンコーダーだけを変えた再出力でも全探索は繰り返しません。

この版の `characters.txt` は12文字で1フレームです。従来の1文字版のターミナルプレイヤーへそのまま渡す形式とは異なります。
