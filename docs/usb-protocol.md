# USB設定プロトコル v1

USB CDCシリアルを使用する。GUIの115200 bpsは仮想COMの設定値でありI2C速度ではない。

1. PCからASCII `HELLO\n` を送信。
2. Picoは `I2C-GATE 1 GPIO-EXPERIMENTAL\n` を返す。
3. PCは設定フレームを送る。
4. PicoはCRC・設定を検証し、通信の切れ目で切り替える。
5. 反映後 `OK XXXXXXXX\n` を返す。XXXXXXXXは受信ペイロードのCRC32（大文字8桁16進）。

## フレーム

| 内容 | 長さ |
| --- | --- |
| ASCII `I2CG` | 4 B |
| ペイロード長、uint32 little endian | 4 B |
| ペイロードのCRC32、uint32 little endian | 4 B |
| 設定ペイロード | 14〜65536 B |

CRCはzlib互換CRC32（反転多項式0xEDB88320、初期値／最終XOR 0xFFFFFFFF）。末尾に改行は付けない。

## 設定ペイロード

先頭はPython struct形式 `<BIIHBBB`。

| 項目 | 型 |
| --- | --- |
| version = 1 | uint8 |
| speed_hz | uint32 LE |
| stretch_timeout_us | uint32 LE |
| max_write_bytes | uint16 LE |
| read_block_fill | uint8 |
| 上流アドレス数 | uint8 |
| ルール数 | uint8 |

上流アドレスを各1バイトで並べ、次にルールを順に並べる。

ルールヘッダは7バイト: enabled、phase、action、上流address、destination、条件数、書き換え数。

- phase: 0=write、1=read_request、2=read_response。
- action: 0=pass、1=modify、2=block。
- address=255は任意、destination=255は維持。
- 条件をすべて、さらに書き換えをすべて並べる。各項目は `<HBB`（offset、value、mask）。
- 名前はPCのJSONに保持し、Picoへ送信しない。

未知のversion、範囲外、余分な末尾、READの時間的に不可能な書き換えを拒否する。不正設定を稼働中設定に反映しない。

## エラーと再接続

応答は `ERR CRC`、`ERR CONFIG`、`ERR LENGTH`、`ERR TIMEOUT`、`ERR BUSY`、`ERR APPLY_PENDING` ＋改行。

受信途中で2秒以上途絶えた場合は破棄する。反映待ちが2秒を超えた場合は `ERR APPLY_PENDING` を返す。この場合は後で反映される可能性があるため、GUIは成功扱いせず反映未確認とする。バスを停止し、受信タイムアウト経過後に再送して成功応答を確認する。

切り替えはRepeated START連鎖の途中では行わない。電源断で設定は失われる。設定の読み戻し、フラッシュ保存、通信キャプチャ転送コマンドは本バージョンに含まない。
