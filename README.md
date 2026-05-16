# mesh2py

3Dメッシュ（STL / OBJ / GLB）から **build123d** のPythonコードを生成する逆エンジニアリングツール。

入力されたメッシュをZ軸方向にスライス・輪郭抽出・特徴分類し、`BuildSketch + extrude` のスタックとして build123d スクリプトを書き出します。生成されるスクリプトは「ゼロからこの部品を作るためのプロンプト（= 編集可能なパラメトリックCADコード）」として、人間やLLMが寸法調整・形状改変・パラメータ化を行う出発点になります。

## 何のため？

- ダウンロードしたSTLや3Dスキャンメッシュには**履歴・パラメータがない**。少し寸法を変えたいだけでもCADソフトでゼロからやり直しになりがち。
- メッシュ→ build123d コード化することで、「Z=0–2mmの楕円ベース」「Z=2–18mmの二翼」のような**構造を読める形**に分解し、特定の寸法だけ書き換えて再生成、といった操作が可能になる。
- 3Dプリンタの標準層厚（0.2mm）で動作するので、出力もそのまま印刷向けに使える精度が出る。

## パイプライン

1. **load**  — trimesh で STL/OBJ/GLB を読込
2. **align** — PCA で最小分散軸をZに合わせ原点へ平行移動
3. **slice** — Z軸を `--slice-step` 刻み（既定 0.2mm）で水平カット、shapely Polygon を抽出
4. **classify** — 各輪郭を `Circle` / `Rectangle` / `Polygon` に分類（穴・島も識別）
5. **segment** — 隣接スライス間の Hausdorff 距離が `--merge-tol` 以下なら同セグメントに統合
6. **codegen** — build123d の `BuildPart` ＋ 各セグメントごとの `BuildSketch + extrude` として出力
7. **validate** — 生成コードを実行し、元STLとの偏差を解析

階段状の近似なので傾斜面はステップになりますが、`--slice-step 0.2` で印刷層厚と整合し、ほとんどのサンプル点が層厚以下に収まります。

## インストール

```bash
git clone https://github.com/qurihara/mesh2py.git
cd mesh2py
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -r requirements.txt
```

依存: `build123d`, `trimesh`, `shapely`, `numpy`, `networkx`, `rtree`, `manifold3d`, `mapbox-earcut`

## 使い方

```bash
.venv/bin/python scripts/mesh2py.py <mesh-file> -o <output.py>
```

### サンプル

```bash
.venv/bin/python scripts/mesh2py.py \
    models/vertical-impact-button-f30-v6.1.stl \
    -o output/vertical-impact-button.py \
    --deviation-ply output/deviation.ply
```

これで以下が生成されます:

- `output/vertical-impact-button.py` — 編集可能な build123d スクリプト
- `output/reconstructed.stl` — 検証用に再エクスポートしたSTL
- `output/deviation.ply` — 元STLとの偏差を頂点色（青=0 → 緑 → 赤=clip以上）で表示するPLY

### CLIオプション

| オプション | 既定値 | 説明 |
|---|---|---|
| `--slice-step` | `0.2` | Z軸スライス間隔（mm）。3Dプリンタ層厚に合わせる |
| `--merge-tol` | `1.5 × slice-step` | 隣接スライス統合の Hausdorff しきい値（mm） |
| `--simplify` | `0.05` | 輪郭シンプル化の許容誤差（mm） |
| `--axis` | auto | `x`/`y`/`z` を明示指定（既定は最小分散軸） |
| `--reconstructed-stl` | `output/reconstructed.stl` | 生成スクリプトのSTL出力先 |
| `--deviation-ply` | (なし) | 偏差マップPLYを書き出すパス |
| `--deviation-clip` | `1.0` | 偏差マップの赤側カラーランプ上限（mm） |
| `--samples` | `8000` | 偏差解析でメッシュ表面から取るサンプル点数（片側） |
| `--no-validate` | off | 生成スクリプトの実行と偏差解析をスキップ |

## 出力例

サンプルモデル `vertical-impact-button-f30-v6.1` (40×65×20mm, 1544三角形) を `--slice-step 0.2` で実行した結果:

```
bbox extents (mm)
    original     :   40.000 x   65.000 x   20.000
    reconstructed:   40.000 x   65.001 x   20.000
    max axis diff: 0.0010 mm

volume        : orig=  7941.747  recon=  7980.829  (Δ= +0.49 %)
surface area  : orig=  6507.550  recon=  6587.080  (Δ= +1.22 %)

surface-to-surface distance (|d|) over 8000 samples per side:
    Hausdorff (max) : 0.8020 mm
    mean            : 0.0086 mm
    RMS             : 0.0490 mm
    median          : 0.0000 mm
    p90 / p95 / p99 : 0.0000 / 0.0432 / 0.1978 mm
    %|d|>0.2mm (orig-side): 0.91 %
```

99%以上のサンプル点が層厚 0.2mm 以下の誤差に収まっています。

## 誤差解析機能

`mesh2py` は実行のたびに以下を自動表示します:

- **bbox** — 各軸の絶対値・差（mm）
- **体積 / 表面積** — 絶対値と差分（%）
- **表面距離** — 両側 N サンプルから:
  - Hausdorff（最大）
  - 平均、RMS、中央値
  - p90 / p95 / p99
  - **層厚超過率**（>0.2mm の割合）
- **符号付き距離** — 「復元が元より内側か外側か」
- **テキストヒストグラム** — 符号付き / 絶対距離の分布
- **偏差マップPLY**（オプション） — MeshLab/Blender で開いて誤差の集中箇所を視覚的に確認

## 既知の制限

- 傾斜・曲面は階段近似になる（生成コード上で `loft` や `revolve` に手動置換可）
- スプライン外形は近似多角形（点列で表現される）
- 連結成分が複数あれば、それぞれ別ポリゴンとして並列に積み上げる
- 回転対称・ミラー対称の自動検出は未実装（今後の課題）

## ライセンス

未設定。リポジトリ所有者にお問い合わせください。
