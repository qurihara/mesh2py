# tinkercad_single/

mesh2py の主要なユースケースである **単一パーツ STL** をここに置きます。

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
