# mesh2py

3Dメッシュ（STL / OBJ / GLB）から **build123d** のPythonコードを生成する逆エンジニアリングツール。

入力メッシュをZ軸方向にスライス→輪郭抽出→特徴分類し、`BuildSketch + extrude` のスタックとして build123d スクリプトを書き出します。生成されるコードは「ゼロから同じ部品を作るためのプロンプト（= 編集可能なパラメトリックCADコード）」として、人間や LLM が寸法調整・形状改変・パラメータ化する出発点になります。

## 想定ユースケース

> **単一パーツの逆エンジニアリング** — Tinkercad で作って STL になってしまった「1個の部品」を、寸法を変えたり機能追加したりできる Python コードに起こす。

- ダウンロードしたSTLや3Dスキャンには履歴・パラメータがない。少し寸法を変えたいだけでもCADソフトでやり直しになりがち
- mesh2py で**読める構造のbuild123dコード**に起こせば、寸法を書き換えて再生成、特徴を `Cylinder()` / `revolve` / `fillet()` に書き換える、といった操作が可能
- 0.2mm 刻みの Z スライスで動作するので、3Dプリンタの標準層厚と整合した精度が出る

### 動かしやすいケース（推奨）

- **単体の連結パーツ** — 1つのワークプレーンに 1部品だけが置かれた状態の STL
- 押し出しベース＋少数の穴
- 1〜2 のテーパー段（高さ方向で形状が緩やかに変わる）
- 回転対称な円筒・テーパー筒（`Cylinder` / `revolve` に自動圧縮される）

### 苦手なケース

- **Tinkercad アセンブリ Export**（複数パーツが同一ワークプレーンに並ぶまま `.STL` 化）→ 各成分のテーパー面が seg爆発（22成分 × 75segs/成分 など）して build123d が時間内に処理しきれない
- 急なテーパー / なめらかな曲面（階段近似で誤差が出やすい）
- 高さ方向 30mm 超 ＋ 全層で形状が変化するモデル

実測の限界例は [models/tinkercad_assembly_sample/](models/tinkercad_assembly_sample/) に残してあります（STL本体はIPのためgitignore）。

### 単体パーツ 10サンプルでの実測（参考値）

Tinkercad のさまざまなデザインから「1パーツだけ選択して Export」した STL を 10件用意して `scripts/batch_check.py` を回した結果（[models/tinkercad_single/README.md](models/tinkercad_single/README.md) も参照）:

| 三角形数 | セグ数 | 例 | 結果 |
|---:|---:|---|:---|
| 234〜384 | 1〜3 | 単純な押し出しパドル / J型アタッチメント | **PASS** (Hausdorff ≤ 0.07mm) |
| 1474 | 14 | 城壁状の角バー | **PASS** (Hausdorff 0.28mm) |
| 1760 | 45 | テーパーボタン + 文字engraving | WARN (Hausdorff 24mm — engraving が破綻要因) |
| 1120 | 5 | 溝付きベースプレート | WARN (p99 0.78mm — 微小角の階段化) |
| 3986 | 234 | DNA らせん | **PASS** (Hausdorff 0.52mm) |
| 6030 | 97 | きのこ型ドーム | **PASS** (Hausdorff 0.91mm) |
| 6122 | 161 | 八角形 + 円柱 | WARN (Hausdorff 1.96mm) |
| 192 | 49 | 小さな立方体に多feature | **FAIL** (OCCT `TopoDS::Face` 例外) |

**集計: PASS 5, WARN 4, FAIL 1**。300 segments を超えても PASS する例（DNA らせん）がある一方、200 三角形でも feature が密だと OCCT が破綻するケースも。

偏差マップは `scripts/gen_deviation_plys.py models/tinkercad_single/` でまとめて生成可、MeshLab/Blender 等で開ける。

## Tinkercad で「単体パーツ」をエクスポートする手順

1. デザインを開く
2. **エクスポートしたいパーツを1つだけ選択**（クリック、または矩形選択で複数同時可）
3. 右上の「エクスポート」を押す
4. ダイアログ上で **`含める: 選択したシェイプ`** にチェック（既定は「デザイン内のすべて」）
5. **`.STL`** を押す

これでそのパーツだけの STL が `~/Downloads/` に落ちます。mesh2py は `models/tinkercad_single/` 配下を想定しています。

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
.venv/bin/python scripts/mesh2py.py <mesh-file.stl> -o <output.py>
```

### サンプル

リポジトリに同梱の単体ボタン部品で動作確認:

```bash
.venv/bin/python scripts/mesh2py.py \
    models/vertical-impact-button-f30-v6.1.stl \
    -o output/vertical-impact-button.py \
    --deviation-ply output/deviation.ply
```

生成されるもの:
- `output/vertical-impact-button.py` — 編集可能な build123d スクリプト
- `output/reconstructed.stl` — 検証用に再エクスポートしたSTL
- `output/deviation.ply` — 元STLとの偏差を頂点色（青=0→緑→赤≧clip）で表示する PLY

実測値: Hausdorff 0.26mm, 体積差 +0.31%, 99%以上のサンプル点が層厚0.2mm以下に収まる。

## パイプライン

1. **load** — trimesh で STL/OBJ/GLB を読込
2. **align** — PCA で最小分散軸を Z に合わせ原点へ平行移動（厚みが他軸より十分小さいときだけ swap）
3. **split** — `mesh.split()` で連結成分に分解＋小さなノイズシェル除去
4. **slice** — Z軸を `--slice-step` 刻み（既定 0.2mm）で水平カット、shapely Polygon を抽出
5. **classify** — 各輪郭を `Circle` / `Rectangle` / `Polygon` に分類（穴・島も識別、小さな文字穴は破棄）
6. **primitive detect** — 成分ごとに Z軸回転対称性をチェック。対称なら `Cylinder` か `revolve` に圧縮（数十 segs → 1op）
7. **segment** — 隣接スライス間の Hausdorff 距離が閾値以下なら同セグメントに統合
8. **codegen** — 成分ごとに `BuildPart` を作って `BuildSketch + extrude` を積む。最後に `Compound` で束ねる
9. **validate** — 生成コードを実行し、元STLとの偏差を解析

階段状の近似なので傾斜面はステップになりますが、`--slice-step 0.2` で印刷層厚と整合し、ほとんどのサンプル点が層厚以下に収まります。

## CLI オプション

| フラグ | 既定 | 説明 |
|---|---|---|
| `-o, --output PATH` | (必須) | 生成する `.py` のパス |
| `--reconstructed-stl PATH` | `output/reconstructed.stl` | 生成スクリプトのSTL出力先 |
| `--slice-step N` | `0.2` | Zスライス間隔（mm）|
| `--simplify N` | `0.05` | 輪郭シンプル化の許容誤差（mm）|
| `--merge-tol N` | `1.5 × slice-step` | 隣接スライス統合の Hausdorff しきい値（mm）|
| `--axis {x,y,z}` | auto | スライス方向を明示指定 |
| `--no-split` | off | 連結成分分解を無効化 |
| `--min-component-volume N` | `50.0` | 連結成分の最小体積（mm³）|
| `--min-component-faces N` | `50` | 連結成分の最小三角形数 |
| `--drop-hole-area N` | `5.0` | N mm² 未満の内側穴を無視（エングレーブ文字対策）|
| `--drop-outer-area N` | `2.0` | N mm² 未満の外側ポリゴンを破棄 |
| `--no-primitive` | off | Cylinder/revolve 検出を無効化 |
| `--loft` | off | テーパー面を `loft` に圧縮（実験的、精度落ちる）|
| `--loft-min-run N` | `10` | loft 化する最小連続セグメント数 |
| `--resample N` | `0` | 各輪郭を N 点に角度等分リサンプル（loft安定化用）|
| `--deviation-ply PATH` | (なし) | 偏差マップ PLY を書き出す |
| `--deviation-clip N` | `1.0` | 偏差マップ赤側カラーランプ上限（mm）|
| `--samples N` | `8000` | 偏差解析の表面サンプル点数（片側）|
| `--no-validate` | off | 生成スクリプト実行＋偏差解析をスキップ |

## 誤差解析

`mesh2py` は実行のたびに以下を自動表示します:

- **bbox** — 各軸の絶対値・差（mm）
- **体積 / 表面積** — 絶対値と差分（%）
- **表面距離** — 両側 N サンプルから:
  - Hausdorff（最大）/ 平均 / RMS / 中央値
  - p90 / p95 / p99
  - **層厚超過率**（>0.2mm の割合）
- **符号付き距離** — 復元が元より内側か外側か
- **テキストヒストグラム** — 符号付き / 絶対距離の分布
- **偏差マップPLY**（`--deviation-ply`）— MeshLab/Blender で開いて誤差の集中箇所を視覚的に確認

## バッチ検証

```bash
.venv/bin/python scripts/batch_check.py models/tinkercad_single/ \
    --hausdorff-max 1.0 --mean-max 0.1 --volume-delta-max 5.0 --p99-max 0.5 \
    --max-segments 500 --build-timeout 300
```

各モデルを `PASS / WARN / FAIL / CRASH / TOO_COMPLEX` に分類した Markdown 表を stdout に出力。

## 偏差マップを一括生成

```bash
.venv/bin/python scripts/gen_deviation_plys.py models/tinkercad_single/
# -> output/single_deviation/<model>_deviation.ply
```

各モデルについて build123d で再構築 → 元STLとの距離を頂点色で塗った PLY を作る。MeshLab / Blender で開いて、誤差がどこに集中しているか（テーパー面の階段か、文字engraving か、特定 feature か）を視覚で確認できる。

## ディレクトリ構成

- `scripts/` — mesh2py 本体とバッチランナー
- `models/tinkercad_single/` — **推奨：単体パーツ STL の置き場所**（STL本体は IP のためリポジトリには含めません）
- `models/tinkercad_assembly_sample/` — アセンブリ Export の限界例置き場（同じく STL は gitignore、READMEで挙動を共有のみ）
- `output/` — 生成された build123d スクリプトと再構築STL（gitignore）

## 既知の制限 / 今後の課題

- 傾斜・曲面は階段近似（生成コード上で `loft` や `revolve` に手動置換可）
- `--loft` は実験的。線形補間で非線形テーパーを近似するため、現状は精度が落ちる
- アセンブリ Export（複数パーツ in 1 STL）は実用域外。Tinkercad 側で単体エクスポートを推奨

## ライセンス

MIT License — 詳細は [LICENSE](LICENSE) を参照。
