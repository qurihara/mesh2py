# tinkercad_single/

mesh2py の主要なユースケースである **単一パーツ STL** をここに置きます。STL本体はIP保護のため gitignore されており、ローカルにのみ保存されます。

## Tinkercad 側での単体エクスポート手順

1. デザインを開く
2. **エクスポートしたいパーツを1つだけ選択**（クリック、または矩形選択）
3. 右上の「エクスポート」を押す
4. ダイアログ上で **`含める: 選択したシェイプ`** にチェック（既定は「デザイン内のすべて」）
5. **`.STL`** を押す

これでそのパーツだけの STL が `~/Downloads/` に落ちます。ここ (`models/tinkercad_single/`) に移動してください。

## 動作確認

```bash
.venv/bin/python scripts/mesh2py.py models/tinkercad_single/<your-part>.stl \
    -o output/<your-part>.py
```

PASS（Hausdorff < 1 mm）ならコードがそのまま使えます。WARN になる場合は誤差解析レポートを見てパラメータ調整。

## バッチでまとめて検証

```bash
.venv/bin/python scripts/batch_check.py models/tinkercad_single/
```

`PASS / WARN / FAIL / CRASH / TOO_COMPLEX` に分類した Markdown 表を出力。

## 偏差マップ一括生成

```bash
.venv/bin/python scripts/gen_deviation_plys.py models/tinkercad_single/
# -> output/single_deviation/<model>_deviation.ply
```

WARN・FAIL の原因が形状のどこにあるかを MeshLab / Blender で目視確認できる。色は **青=0mm → 緑=中間 → 赤=clip(既定1mm)以上**。

## サンプル分布（10件、`batch_check.py` 実測）

| カテゴリ | 件数 | 例 |
|---|---:|---|
| **PASS** (Hausdorff<1mm 等しきい値クリア) | 5 | パドル、J型アタッチ、城壁バー、DNA らせん、きのこドーム |
| **WARN** (build123d は完走、ただし誤差過大) | 4 | テーパーボタン(engraving), 八角+円柱, 溝付きベース, 誤選択cube |
| **FAIL** (build123d 例外) | 1 | 小さな立方体に多feature → OCCT `TopoDS::Face` |

偏差マップを開けば、PASS の中でも階段近似がどこに乗っているか、WARN の崩れがどの面に集中しているかが一目で分かる。
