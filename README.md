# I2C-Gate

Raspberry Pi Pico（RP2040）をI2Cホストとデバイスの間に接続し、宛先やデータを変更する試作です。Windows GUI、JSON設定、USB設定転送、GPIOを直接制御するファームウェアのソースを含みます。

**GUIとフィルタ処理はテスト済みです。Pico向けARMビルドとUF2生成を確認済みです。実機USB通信、I2C波形、400 kHz動作は未検証です。完成・動作保証済みの中継器ではありません。**

## Windows GUIを起動

Python 3.9以降（tkinter付き）を使用します。

Pico拡張付属の `.pico-sdk/python` は組み込み用Pythonでtkinterを含まないため、GUIには使えません。通常版Pythonをインストールしてください。

```powershell
winget install --id Python.Python.3.13 --exact --source winget --scope user
```

インストール後はターミナルを開き直して、次を実行します。

```powershell
py -m pip install -r requirements.txt
py run_gui.py
```

USB機能を使用しない場合は追加パッケージ不要です。「開く」で `examples/filters.json` を読み込んで試せます。

- ルール一覧、追加・編集・複製・削除、有効／無効、優先順位の変更。
- アドレス、WRITE／READ、バイト位置・値・マスクの条件。
- 通過、部分書き換え、遮断、下流アドレスの変更。
- JSONファイルの読み込み・保存と全設定の編集。
- オフライン検証で一致ルール、変換結果、下流アクセスの有無を表示。
- COMポートを選び、現在の設定をUSB経由でPicoのRAMへ送信。

保存とUSB送信は別の操作です。送信開始時の設定を反映し、Picoの確認応答を受けた場合のみ成功と表示します。RAM保持のため、再起動後は再送信が必要です。実通信のキャプチャ表示機能は含みません。

## 配線

```text
ホスト SDA/SCL ── Pico 上流target / フィルタ / 下流controller ── デバイス SDA/SCL
                         GP0,GP1                GP2,GP3
```

| 接続先 | Pico GPIO | Pico物理ピン |
| --- | --- | --- |
| ホスト SDA | GP0 | 1 |
| ホスト SCL | GP1 | 2 |
| デバイス SDA | GP2 | 4 |
| デバイス SCL | GP3 | 5 |
| 共通GND | GND | 3など |

上下流のSDA/SCLを電気的に分離し、それぞれにプルアップを設けます。GPIOはLow出力と入力切り替えによるオープンドレイン制御です。3.3 V系を前提とし、5 Vバスとの接続にはレベル変換が必要です。抵抗値は配線容量・既存のプルアップ・速度に合わせて決めてください。[RP2040データシート](https://datasheets.raspberrypi.com/rp2040/rp2040-datasheet.pdf)

## 中継方式

同じPico内のGP4/GP5では、フィルタと独立した疑似I2Cデバイスが常時動作します。アドレス `0x50` へWRITEした値をFIFO順にREADでき、空なら `0xFF` が返ります。ファームウェアは `build/pico/i2c_gate.uf2` の1種類です。下流ピンからジャンパ線で疑似デバイスへ接続してデバッグできます。[配線と確認手順](docs/echo-stub.md)

ホストとデバイスのクロックストレッチ対応を前提にします。上流400 kHzまでを開発目標としますが、GPIOポーリングによる試作で、取りこぼしやセットアップ時間は未測定です。下流の実効速度は設定値以下で、中継待ちによって通信全体の速度も低下します。要件を満たせない場合は上流監視のPIO化が必要です。

- Core 1: 割り込みを無効化し、上流GPIOの監視、START／STOP／Repeated START、バイト送受信を処理。
- Core 0: 下流GPIO制御、USB処理、設定の検証。
- アドレス: 8ビット受信 → 上流SCLをLowに保持 → コールバック → 許可時だけ下流へアドレス送信 → 下流ACK/NACKを上流へ返却。遮断時は下流へ送らずアドレスNACK。
- READ: 下流から8ビット受信 → 上下流SCLをLowに保持 → コールバックで現在バイトの変更とACK方針を決定 → ホストへ送信 → 指定したACK/NACKを下流の9クロック目に送信。
- WRITE: 1バイトの8ビット受信ごとに上流SCLをLowに保持 → コールバック → 許可時は現在バイトを下流へ送信 → 下流ACK/NACKを上流へ返却。遮断時はそのバイトを送らずNACK。
- Repeated STARTを含む一連の通信中は同じ設定を使い、新しい設定は通信の切れ目で反映します。

I2C転送にSDKの一括READ／WRITE APIは使用しません。SDKは起動、USB、コア起動、時刻、GPIO初期化などに使用します。ACK線を直結するのではなく、独立したバス間でACK/NACKの値を再送します。[I2C仕様](https://www.nxp.com/docs/en/user-guide/UM10204.pdf)

## ルールと遮断の意味

既定動作は通過です。設定未送信の起動直後・ルールなし・一致するルールなしの場合は、元のアドレスとデータで中継します。一致したルールだけが変更や遮断を行います。対応範囲は通常の7ビットアドレス `0x08〜0x77` です。アドレスの事前登録は不要で、旧設定の `bus.addresses` は読み込み時に取り除きます。WRITEの受信履歴には設定された長さ上限を適用します。コールバックAPIは[バイト単位コールバック](docs/byte-callbacks.md)を参照してください。

ルール編集画面はJSONの手入力を使わず、次の順で設定します。

1. **通信の種類**：`write`、`write→read`、`read` を選択。
2. **一致条件**：I2Cアドレスと、データの位置・値・マスクを表に入力。複数行はAND条件です。
3. **ACK/NACK・書き換え**：応答方法、変更後の下流アドレス、データ位置・設定値・AND/ORを指定。

| 通信の種類 | 一致条件と適用先 |
| --- | --- |
| write | ホストから受けたWRITEのアドレス・データで判定し、WRITEを変更 |
| write→read | 先行WRITEのアドレス・データと後続READの条件で判定し、READに適用。Repeated START区間内のみ有効でSTOPで解除 |
| read | READのアドレス・データで判定。先行WRITEの有無は問わない |

アドレス欄には7ビット値（例：`0x50`）を入力します。R/Wビットを含む `0xA0` / `0xA1` ではありません。アドレスの `*` は任意、データ条件の行がなければデータ条件なしです。バイト位置は0始まり、値とマスクは `0x10` / `0xFF` のように指定します。

| ACK/NACK | WRITE | READ / write→readのREAD |
| --- | --- | --- |
| 自動 | 下流デバイスの応答をホストへ中継 | ホストのACK/NACKを下流へ中継 |
| アドレスでNACK | ホストへNACKし、下流へアドレスを送らない | READアドレスをホストへNACK |
| 指定データバイトでNACK | 指定位置を下流へ送らずホストへNACK | 指定バイトを受信後、下流デバイスへNACK。ホストがさらに要求した場合は代替値を返す |

READデータのACK/NACKを返すのはホストです。そのためREADでの「指定データバイトでNACK」は下流側の読み出し終了を指定します。指定位置より前のデータは通常どおり転送され、書き換えも併用できます。WRITEのNACK対象バイトは転送しないので、その位置の書き換えは指定できません。

例えば「WRITEでレジスタ `0x10` を指定した後、READの4バイト目を `0x5A` にする」場合：
- 通信の種類：`write→read`
- 先行WRITE条件：アドレス `0x52`、データ位置 `0`・値 `0x10`・マスク `0xFF`
- READ条件：アドレス `0x52`
- ACK/NACK：自動
- 書き換え：位置 `3`・AND・値 `0x00`、続けて位置 `3`・OR・値 `0x5A`

WRITE側も変更したい場合は別の `write` ルールを設定します。先行WRITEの条件判定には、書き換え前のホスト送信データを使います。

アドレス応答はデータを受信する前に決まるため、同じ通信のデータ条件でアドレスNACKや宛先変更はできません。ただし `write→read` の先行WRITE条件で後続READアドレスを判定できます。READの宛先変更とREADデータ変更は別ルールで指定してください。未来のバイトを条件に過去のバイトを書き換える設定や、NACK位置より後の条件も拒否します。

設定ファイルの `phase` は内部の実行タイミングを表し、画面では通信の種類に変換します。変更は `modify`、アドレスNACKは `block` として保存されます。`pass` は使用しません。既存のACK強制・READ代替値への遮断ルールは、そのルールを編集するときだけ専用の選択肢として表示されます。

指定位置NACKには更新済みファームウェアが必要です。GUIを再起動し、新しいUF2を書き込んでから設定を送信してください。

| phase | 判定するもの | blockの動作 |
| --- | --- | --- |
| `write` | アドレス、および当該バイトまでの元のWRITEデータ | 一致したアドレス／データバイトを送らず、その位置でNACK |
| `read_request` | READ開始時の上流アドレス | アドレスNACK。下流READなし |
| `read_response` | 当該バイトまでの元のREADデータ | 条件成立バイト以降を代替値にする |

WRITEのアドレスと各データのACK/NACKは、下流の応答が確定してからホストへ返します。途中で遮断しても、それ以前に送ったデータとデバイスへの副作用は取り消せません。NACK後の同一WRITE区間では追加データを転送せず、STOPまたはRepeated STARTを待ちます。

READ応答の条件で遮断する場合、すでに返した先行バイトは回収できず、下流の読み出しやレジスタ／FIFOへの副作用も残ります。遮断後もホストのACK/NACKを中継して要求された分を読み、ホストには代替値を返します。下流アクセス自体を止めるには `read_request` の遮断を使用します。

`write` と `read_response` は**各バイト時点で**条件が判明しているルールの最初の一致を採用します。後で成立する上位ルールは、すでに返したバイトを変更しません。READの既存block（代替値への置換）は成立後、READ終了まで維持します。条件位置より前を書き換えるルールは設定エラーです。

一致条件は `(byte & mask) == (value & mask)` です。書き換えは **元データ AND 設定値** または **元データ OR 設定値** を選びます。書き換え欄にはマスクを指定しません。例えば `0xAB AND 0xF0 = 0xA0`、`0xAB OR 0x05 = 0xAF` です。同じバイト位置の複数行は上から順に演算し、`AND 0xF0` → `OR 0x05` なら `0xAB` は `0xA5` になります。

旧設定のマスク式書き換えは、編集時に結果が同じになるAND→ORの2行へ変換します。一致条件は常に元データで照合し、データ長は変更しません。未受信の条件は不一致です。将来の書き換え位置があるルールも現在までの条件で照合し、書き換えるのは現在バイトだけです。

宛先変更はREADでは `read_request`、WRITEでは `write` に指定します。READ応答後には変更できません。先行WRITEとREADで別宛先へ変換するとレジスタ指定が共有されないため、対応するルールを利用者が揃える必要があります。

## JSON設定

```json
{
  "version": 1,
  "bus": {
    "speed_hz": 400000,
    "stretch_timeout_us": 25000,
    "max_write_bytes": 256,
    "read_block_fill": "0xFF"
  },
  "rules": [
    {
      "name": "レジスタ0x10へのWRITEを変更",
      "enabled": true,
      "phase": "write",
      "match": {
        "address": "0x50",
        "payload": [{"offset": 0, "value": "0x10"}]
      },
      "action": "modify",
      "patches": [{"offset": 1, "operation": "AND", "value": "0x00"}]
    }
  ]
}
```

数値は整数または `"0x50"` のような文字列、任意アドレスは `"*"`。一致条件のmask省略時は255です。書き換えのoperationは `"AND"` または `"OR"` で、maskと併用できません。ルール最大64、1ルールの条件は64、書き換えは128演算・64バイト位置まで、参照位置は0〜4095。WRITE上限は1〜4096バイトで、上限を超えた最初のバイトでNACKを返し、そのバイト以降を送信しません。JSONはUTF-8で最大64 KiBです。

`stretch_timeout_us` はGPIO待機や下流処理のタイムアウトにも使います。1バイトの転送時間とホストの許容ストレッチ時間を考慮してください。サンプルは仮の100 kHz・25 msです。オフライン検証の入力は最大4096バイト、実機READはホストNACKまたは下流への強制NACKで終了します。READ履歴の上限4096バイトでは下流へNACKを返します。下流終了後もホストが要求する場合は `read_block_fill` を返し、追加の下流READは行いません。

## Picoファームウェアのビルド

このWindows環境では、インストール済みのPico拡張のツールを使って、プロジェクト直下から実行できます。

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/build_pico.ps1
```

VS Codeでは **Ctrl+Shift+B**（`Build Pico firmware`）でも同じビルドを実行できます。CMake Toolsを使う場合は、構成プリセット `pico` を選択してください。

Raspberry Pi Pico拡張を使う場合は、このリポジトリのルートフォルダをVS Codeで開いてください。設定反映後に **Developer: Reload Window** を実行するとPicoプロジェクトとして認識されます。拡張の **Run Project** ボタンでビルド後にUSB経由で書き込み・起動します。初回はPicoのBOOTSELを押しながらUSB接続してください。書き込み対象は `build/pico/i2c_gate.uf2` です。拡張の **Compile Project** ボタンも既存のビルドスクリプトを使用します。

拡張が認識するSDK・ツールチェーン・ボード設定とファームウェアのビルド定義はルートの `CMakeLists.txt` にあります。`firmware/CMakeLists.txt` はこれを読み込み、従来のプリセットと `cmake -S firmware` に対応します。

`firmware/CMakePresets.json` は `%USERPROFILE%/.pico-sdk` 配下のSDK 2.3.0、Arm GNU Toolchain 15_2_Rel1、Python 3.13.7、Ninja v1.13.2、picotool 2.3.0を参照します。スクリプトは同じ場所のCMake v4.3.4を優先し、なければPATHのCMakeを使います。別PCでツールのバージョンが異なる場合はプリセットとスクリプトのパスを合わせてください。

独自に用意したツールを使う場合は、以下の手順も利用できます（プリセットと異なるツールを使う際は別のビルドディレクトリを指定してください）。

Pico C/C++ SDK、Arm GNU Toolchain（`arm-none-eabi-gcc`）、CMake、Ninjaを用意します。SDKのサブモジュール（TinyUSBなど）も必要です。

```powershell
$env:PICO_SDK_PATH = 'C:\path\to\pico-sdk'
cmake -S firmware -B build/pico -G Ninja -DPICO_BOARD=pico -DCMAKE_BUILD_TYPE=Release
cmake --build build/pico
```

成功すると `build/pico/i2c_gate.uf2` が生成されます。PicoをBOOTSELモードで接続してUF2をコピーします。起動直後から全対応アドレスをルールなしで中継します。変更・遮断を適用する場合はGUIでCOMポートを選んで設定を送信してください。旧ファームウェアから起動時の動作を変えるには、新しいUF2の書き込みが必要です。

USB上ではJSONをPC側で検証し、コンパクトなバイナリ表に変換して送ります。Picoも範囲・CRC・ルールを検証します。[USBプロトコル](docs/usb-protocol.md)

## 検証状況と制約

```powershell
py -m unittest discover -s tests -v
```

Visual Studio C++ Build Toolsがある場合は、C側も比較できます。

```powershell
.\scripts\build_native.ps1
py -m unittest discover -s tests -v
```

C側のデコーダ／フィルタ、ブリッジ状態機械、スタブのFIFOをWindows DLLにビルドして検証します。Python側との300ケースの比較、CRC、不正・切断データ、FIFO境界、GUI編集に加え、模擬バスでバイト送出順序・ACK中継・ストレッチ中のコールバック・Repeated STARTコンテキストを確認します。DLL未生成時はCのテストをスキップします。既存DLLがある場合も、C側を変更した後は再ビルドしてください。

Pico SDK 2.3.0とArm GNU Toolchain 15.2.Rel1を使用し、Release構成のARMビルドと `build/pico/i2c_gate.uf2` の生成を確認しました。実機への書き込みと動作確認は未実施です。USBテストは通信相手を模擬したものです。

初期対応範囲は単一ホスト・7 bit通常アドレスです。10 bit、General Call、マルチホスト、SMBus PEC自動再計算、電源断後の設定保持は未実装です。未知のプロトコルのレジスタ状態は仮想化しません。Repeated START後のREADはアドレスコールバックで個別に判断します。先行WRITEが途中遮断された場合、受信済みの元データ（遮断バイトを含む、長さ上限以内）がコンテキストになります。

実機では上下流4信号を同時観測し、400 kHzのエッジ捕捉、セットアップ時間、ホストNACK後の余分なREADがないこと、Repeated START、WRITE遮断時の漏れ、設定変更、タイムアウト復帰を検証する必要があります。
