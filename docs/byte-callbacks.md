# バイト単位のコールバック

`bridge_init()` 後、`multicore_launch_core1(bridge_core1)` 前に
`bridge_set_callbacks(address_fn, data_fn)` で登録します。NULLは標準フィルタへ戻します。
PCからのUSB往復ではなく、PicoのCore 1上で同期実行します。実行中の登録変更はできません。
定義は `firmware/gate_config.h`、登録APIは `firmware/gpio_bridge.h` にあります。

```c
address_result_t address_fn(const config_t *cfg, uint8_t address, bool read,
                            const write_context_t *write);
data_result_t data_fn(const config_t *cfg, uint8_t address, bool read,
                      const uint8_t *data, size_t count, const write_context_t *write);
```

- `address` はホストが送信した7ビットアドレス、`read` はR/Wビットです。
- アドレスコールバックは8ビット受信後、9ビット目のACK前に呼びます。`block=true` ならそのアドレスを下流へ送りません。`destination` は通常 `address` を設定します（既存の宛先変更機能も維持）。
- WRITEデータコールバックはホストからの8ビット受信後、下流送出・ホストACK前に呼びます。`data[0..count-1]` は現在バイトを含む、ホストから受信した元のデータです。`block=true` なら現在バイトを送らずNACK。そうでなければ `modify=true` のときだけ `value` に置き換えて送信し、下流ACK/NACKを返します。blockはmodifyより優先します。
- READデータコールバックはデバイスから8ビット受信後、ホストへ送る前に呼びます。配列はデバイスから受信した元データです。`modify/value` はホストへ返す現在バイトを変更します。
- READの `ack` は `ACK_HOST`（ホストACK/NACKをそのまま転送）、`ACK_FORCE`、`NACK_FORCE` の3種類です。実際の下流9クロック目はホストの応答を受けてから生成します。WRITE時はこのフィールドを使用しません。

コールバック中は上流SCLをLowで保持します。WRITE/アドレスは8ビット目の下降エッジでストレッチを開始し、下流ACK/NACK確定まで保持します。READはホストへの送信前に上流SCLを保持し、下流SCLも8ビット受信後から9ビット目までLowで保持します。

コールバックは割り込み無効のCore 1で実行するため、USB待ち・スリープ・ブロッキングI/Oを行わず、ホストが許容する時間内で戻してください。`stretch_timeout_us` はGPIOと下流ジョブの待機上限であり、ユーザー定義コールバックを強制中断する機能ではありません。ポインタは呼び出し中のみ有効で、データを書き換えたり保持したりしないでください。

## Repeated STARTのコンテキスト

同じSTART～STOPの連鎖で直近に受け付けたWRITEがあれば、READのアドレス／データコールバックで `write->valid` がtrueになります。`write->address` と `write->data[0..count-1]` はそのWRITEの元のアドレスと元のデータです。WRITEへのmodify結果ではありません。アドレスだけのWRITEもcount=0で有効です。通常のREADではvalid=false、WRITEデータコールバックではNULLです。

STOP、タイムアウト、新しいWRITE区間の開始で以前のコンテキストを破棄します。WRITEの途中NACK時は、上限以内の受信済みデータ（遮断／デバイスNACKのバイトを含む）を保持します。その後のREADを遮断するかどうかはREADアドレスコールバックで判断します。先行WRITEの副作用は巻き戻せません。

## GUI・JSON設定

標準コールバックは各バイトで上から順にルールを評価し、その時点で条件が成立する最初のルールを採用します。アドレス判定ではWRITEのデータ書き換えのみのルールは対象外です。WRITEのデータ条件付き宛先変更、WRITE/READで将来のバイトを条件に過去のバイトを書き換える設定は拒否します。

READルールに `match.write` を指定すると先行WRITEの存在と内容を条件にできます。現在のREADアドレスが `match.address`、READデータが `match.payload`、先行WRITEのアドレスとデータが `match.write` です。READアドレス判定では現在のREADデータはまだないため `match.payload` は空にします。

```json
{
  "name": "レジスタ0x10を指定したREADの先頭を変更",
  "enabled": true,
  "phase": "read_response",
  "match": {
    "address": "0x50",
    "payload": [],
    "write": {"address": "0x50", "payload": [{"offset": 0, "value": "0x10"}]}
  },
  "action": "modify",
  "patches": [{"offset": 0, "value": "0x42"}],
  "ack": "host"
}
```

`ack` は省略時 `host`、`ack` で強制ACK、`nack` で強制NACKです。データを書き換えずACKだけ指定する場合もactionは `modify`、patchesは空配列にします。GUIのルール編集画面でACKと先行WRITE条件を編集できます。検証画面の「READ前にWRITE → repeated START」で複合転送を模擬します。Pythonからは `simulate_combined()` を使用できます。

## 終了と上限

- WRITEは `max_write_bytes` の次のバイトにNACKを返し、そのバイトを送りません。以前のバイトは転送済みです。
- READの履歴上限は4096バイトです。上限バイトの下流応答はACK設定より優先してNACKになります。
- 下流へNACKを送った後、ホストがさらにACKを返して読み続ける場合は `read_block_fill` を返します。追加の下流READやデータコールバックは行いません。STOP／Repeated STARTを受ければ次の区間へ進みます。
- 強制ACKはホストの最終NACKも上書きします。ブリッジは余分なREADクロックを生成せずホストのSTOP／Repeated STARTを下流へ伝えますが、デバイス側では次バイトの準備・FIFO消費が起こり得ます。次バイトのSDAを保持するデバイスではその境界を正常に伝えられない場合があるため、終端バイトにも強制ACKする設定はデバイスの挙動を実機で確認してください。
- 既存のREAD `block` は互換機能として残しています。成立したバイト以降をfillに置換し、下流の読み出しは指定ACK方針に従います。下流アクセス自体を始めない場合は `read_request` のblockを使います。

模擬バステストはC状態機械の順序とストレッチ保持状態を検証します。GPIOのエッジ捕捉・セットアップ時間・実機のストレッチは別途ロジックアナライザで確認が必要です。
