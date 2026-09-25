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
- READ: 下流から8ビット受信 → 下流SCLをLowで保持 → データを加工して上流へ送信 → ホストのACK/NACKを下流の9クロック目で再送。ACKの場合のみ次のデータを読みます。総READ長の事前指定は不要です。
- WRITE: 上流から全体をバッファ受信 → STOP／Repeated STARTでルール判定 → 下流へ送信または破棄。
- Repeated STARTを含む一連の通信中は同じ設定を使い、新しい設定は通信の切れ目で反映します。

I2C転送にSDKの一括READ／WRITE APIは使用しません。SDKは起動、USB、コア起動、時刻、GPIO初期化などに使用します。ACK線を直結するのではなく、独立したバス間でACK/NACKの値を再送します。[I2C仕様](https://www.nxp.com/docs/en/user-guide/UM10204.pdf)

## ルールと遮断の意味

| phase | 判定するもの | blockの動作 |
| --- | --- | --- |
| `write` | ホストから受信したWRITE全体 | 全体を破棄し、下流へ送らない |
| `read_request` | READ開始時の上流アドレス | アドレスNACK。下流READなし |
| `read_response` | 当該バイトまでの元のREADデータ | 条件成立バイト以降を代替値にする |

WRITEでは後続ペイロードを得るため先行バイトにACKを返します。後から遮断してもそのACKは取り消せず、下流のNACKを過去の上流ACKへ反映することもできません。WRITEのACKを忠実に中継するモードは未実装です。

READ応答の条件で遮断する場合、すでに返した先行バイトは回収できず、下流の読み出しやレジスタ／FIFOへの副作用も残ります。遮断後もホストのACK/NACKを中継して要求された分を読み、ホストには代替値を返します。下流アクセス自体を止めるには `read_request` の遮断を使用します。

`read_response` は**各バイト時点で**条件が判明しているルールの最初の一致を採用します。後で成立する上位ルールは、すでに返したバイトを変更しません。blockは成立後、READ終了まで維持します。条件位置より前を書き換えるルールは設定エラーです。

条件は `(byte & mask) == (value & mask)`、書き換えは `(byte & ~mask) | (value & mask)`。元データで照合し、長さは変更しません。WRITEでは条件位置や書き換え位置が実データの範囲外なら不一致です。READでは将来の書き換え位置を待ちながらバイトごとに処理します。

宛先変更はREADでは `read_request`、WRITEでは `write` に指定します。READ応答後には変更できません。`bus.addresses` に代理応答する上流アドレスを指定し、その他はNACKです。先行WRITEとREADで別宛先へ変換するとレジスタ指定が共有されないため、対応するルールを利用者が揃える必要があります。

## JSON設定

```json
{
  "version": 1,
  "bus": {
    "speed_hz": 400000,
    "stretch_timeout_us": 25000,
    "max_write_bytes": 256,
    "addresses": ["0x50"],
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
      "patches": [{"offset": 1, "value": "0x00"}]
    }
  ]
}
```

数値は整数または `"0x50"` のような文字列、任意アドレスは `"*"`。mask省略時は255です。ルール最大64、1ルールの条件／書き換えは各64、参照位置は0〜4095。WRITE上限は1〜4096バイトで、超過したWRITEは全体を破棄します。JSONはUTF-8で最大64 KiBです。

`stretch_timeout_us` はGPIO待機や下流処理のタイムアウトにも使います。長いWRITE全体を送信できる時間とホストの許容待ち時間の両方を考慮してください。サンプルは仮の100 kHz・25 msです。オフライン検証の入力は最大4096バイト、実機READの終了はホストNACKで決まります。

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

成功すると `build/pico/i2c_gate.uf2` が生成されます。PicoをBOOTSELモードで接続してUF2をコピーし、通常起動後にGUIでCOMポートを選んで設定を送信します。起動直後は代理応答するアドレスが空で、設定を受け取るまでACKしません。

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

本環境では36テストが成功しました。C側のデコーダ／フィルタとスタブのFIFOをWindows DLLにビルドし、Python側との300ケースの比較、CRC、不正・切断データ、FIFOの境界条件の検証、GUIの編集・並べ替え・結果表示を含みます。DLL未生成時はCの9テストをスキップします。既存DLLがある場合も、C側を変更した後は再ビルドしてください。

Pico SDK 2.3.0とArm GNU Toolchain 15.2.Rel1を使用し、Release構成のARMビルドと `build/pico/i2c_gate.uf2` の生成を確認しました。実機への書き込みと動作確認は未実施です。USBテストは通信相手を模擬したものです。

初期対応範囲は単一ホスト・7 bit通常アドレスです。10 bit、General Call、マルチホスト、SMBus PEC自動再計算、電源断後の設定保持は未実装です。未知のプロトコルのレジスタ状態は仮想化しません。遮断／失敗したWRITEに同じRepeated START連鎖で続くREADはNACKします。

実機では上下流4信号を同時観測し、400 kHzのエッジ捕捉、セットアップ時間、ホストNACK後の余分なREADがないこと、Repeated START、WRITE遮断時の漏れ、設定変更、タイムアウト復帰を検証する必要があります。
