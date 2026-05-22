# tinkercad_assembly_sample/

mesh2py の **限界例** として保存している、Tinkercad の「複数パーツを1ワークプレーンに並べたまま `.STL` Export した」モデル群です。

このまま `scripts/batch_check.py` に通すと:

| 結果カテゴリ | 件数 |
|---|---:|
| PASS | 3（うちこの dir に居るのは単体換算で 3 件分）|
| WARN | 1 |
| TOO_COMPLEX | 7 |
| CRASH | 3 |

主な失敗パターンは「1つの STL に複数パーツが入っているせいで `mesh.split()` 後の各成分のテーパー面が 75 segs ずつ生成され、合計 800〜1200 ops に達して build123d が時間内に処理しきれない」というもの。

実用上は **パーツを1個だけ選択してエクスポート**するのが推奨ワークフロー（`models/tinkercad_single/README.md` 参照）。このディレクトリは「アセンブリ Export だとなぜダメなのか」のリグレッションテストとして温存しています。

## 再実行

```bash
.venv/bin/python scripts/batch_check.py models/tinkercad_assembly_sample/
```
