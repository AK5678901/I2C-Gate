# USB設定プロトコル（設定ペイロード v1 / v2 / v3）

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
| version = 1、2、3 | uint8 |
| speed_hz | uint32 LE |
| stretch_timeout_us | uint32 LE |
| max_write_bytes | uint16 LE |
| read_block_fill | uint8 |
| 上流アドレス数 | uint8 |
| ルール数 | uint8 |

上流アドレスを各1バイトで並べ、次にルールを順に並べる。

v1のルールヘッダは7バイト: enabled、phase、action、上流address、destination、条件数、書き換え数。

- phase: 0=write、1=read_request、2=read_response。
- action: 1=modify、2=block（0=passは拒否）。
- address=255は任意、destination=255は維持。
- 条件をすべて、さらに書き換えをすべて並べる。各項目は `<HBB`（offset、value、mask）。
- 名前はPCのJSONに保持し、Picoへ送信しない。

GUI/JSONの書き換えは `operation: AND/OR` と `value` で指定します。PC側で同じバイト位置の演算を上から順に合成し、既存の `<HBB>` 書き換え形式へ変換します。ANDの値をvとするとvalue=0・mask=~v（8ビット）、ORならvalue=v・mask=vです。複数演算も同じ位置につき1項目にまとめるため、Picoの既存のビット書き換え処理で同じ結果になります。今回の演算指定の追加ではUSB形式は変更しません。

v2は上記7バイトの直後に4バイトを追加します: `ack`（0=host、1=ACK、2=NACK）、先行WRITE条件の有無（0/1）、先行WRITEアドレス（255=任意）、先行WRITE条件数。通常の条件と書き換えの後に、先行WRITE条件を同じ `<HBB` 形式で並べます。ACK指定はread_responseのみ、先行WRITE条件はread_request/read_responseのみです。先行WRITE条件なしの場合は有無=0、アドレス=255、条件数=0です。

v3ではv2の11バイトのルールヘッダ直後に `nack_at`（uint16 LE）を追加します。0〜4095はNACKを開始するデータ位置（0始まり）、65535は位置指定なしです。位置指定は `modify` / `ack=nack` でのみ使用でき、現在の通信の条件位置はNACK位置以下に制限します。WRITEはその位置を下流へ送らず上流へNACK、READはその位置を受信後に下流へNACKします。先行WRITE条件はこの位置制限の対象外です。v3ではWRITEの `ack=nack` も受理します。自動ACKに戻す位置指定や強制ACK付きの位置指定は拒否します。

PCは `nack_at` またはWRITEの `ack` を含む設定をv3で送信します。それ以外では `ack` または `match.write` を含む設定をv2、それ以外はv1で送信します。更新版ファームウェアは3形式を受理します。旧ファームウェアは新しい形式を拒否するため、利用前にUF2を更新してください。JSONのversionは引き続き1です。

未知のversion、範囲外、余分な末尾、WRITE/READの時間的に不可能な書き換え、WRITEデータを条件にした宛先変更を拒否する。不正設定を稼働中設定に反映しない。

## エラーと再接続

応答は `ERR CRC`、`ERR CONFIG`、`ERR LENGTH`、`ERR TIMEOUT`、`ERR BUSY`、`ERR APPLY_PENDING` ＋改行。

受信途中で2秒以上途絶えた場合は破棄する。反映待ちが2秒を超えた場合は `ERR APPLY_PENDING` を返す。この場合は後で反映される可能性があるため、GUIは成功扱いせず反映未確認とする。バスを停止し、受信タイムアウト経過後に再送して成功応答を確認する。

切り替えはRepeated START連鎖の途中では行わない。電源断で設定は失われる。設定の読み戻し、フラッシュ保存、通信キャプチャ転送コマンドは本バージョンに含まない。
