# 低溫量測分析工具（Low-Temperature Analysis）

把 `Analysis.ipynb` 裡的分析整理成一個可以重複使用的工具，有兩種用法：

- **分析介面**（選單 1）：開啟 .db 後一次讀進所有 run，分成「1D 資料分析」「2D 資料分析」兩頁，選 run、選功能、調參數，圖馬上更新。
- **批次分析**（選單 4、5）：參數寫在 `jobs\*.toml`，一次重新產生某個樣品的所有圖和 `index.html`。

不需要安裝 qcodes（直接讀 .db），也不會修改原始資料。

```
LowTempAnalysis\
├─ install.bat           ← 第一次雙擊，安裝成軟體（開始功能表 + 桌面圖示）
├─ run_analysis.bat      ← 開啟軟體（可複製到任何地方）
├─ tools.bat             ← 文字選單（批次分析、資料總表等）
├─ lta_env.bat           ← 尋找 Python（給上面的啟動檔用）
├─ release.json          ← 版本號與 GitHub 更新來源
├─ 發佈與更新.md          ← 透過 GitHub 發佈新版、讓其他人自動更新
├─ settings.toml         ← 這台電腦的設定（第一次開啟時由 settings.example.toml 自動建立）
├─ 擴充指南.md            ← 之後要加功能、新量測方式時看這份
├─ jobs\                 ← 批次分析設定檔（每個樣品一個）
├─ lta\                  ← 程式本體（Python 原始檔，可以直接改）
│   ├─ core\             資料層：讀 .db、量測模式（I→V、V→I）
│   ├─ analysis1d\       1D 分析功能（一個功能一個檔案）
│   ├─ analysis2d\       2D 分析功能
│   ├─ gui\              分析介面
│   └─ batch\            批次分析
├─ assets\               ← 圖示（icon.svg 原稿、icon.ico、各尺寸 png）
├─ Explore.ipynb         ← 在 Jupyter 自由探索、測試新功能
└─ catalog.xlsx          ← 「更新資料總表」產生的所有 run 一覽
```

## 第一次使用

1. 把整個資料夾**解壓縮**到固定位置，例如 `D:\Yeep\02 Low Temperature Analysis\`（不要在 zip 裡直接執行）。
2. **雙擊 `install.bat`（只要做一次）**：會在「開始」功能表和桌面加上「低溫量測分析」。之後就像一般軟體一樣，從開始功能表搜尋或雙擊桌面圖示開啟，不會有黑色視窗，也可以釘選到工作列。
3. 第一次開啟後，到 **⚙ 設定 → 資料與輸出** 選擇放樣品資料的資料夾。

三個啟動檔：

| 檔案 | 用途 |
|---|---|
| `run_analysis.bat` | 開啟軟體（黑色視窗會馬上關掉，軟體獨立執行）。可以複製到任何地方使用；把 .db 拖上去會直接用它開啟 |
| `tools.bat` | 文字選單：批次分析、資料總表、建立批次設定檔…（軟體裡的「▾ 工具」也有同樣的功能） |
| `install.bat` | 建立開始功能表與桌面捷徑 |

需要的 Python 套件：numpy、scipy、matplotlib、tkinter（Anaconda 都內建）。啟動檔會自動尋找電腦上的 Python（Anaconda、Miniconda、python.org…）並檢查套件；找不到時會說明原因，並可以自動安裝。

**自動更新**：軟體開啟時會到 GitHub 檢查新版本，有的話跳出提示，按「是」就自動更新並重新開啟（設定與資料不會被覆蓋）。發佈方式請看 **`發佈與更新.md`**。

## 分析介面

```
┌─────────────────────────────────────────────────────────────────────────────┐
│ ◢ 低溫量測分析   資料庫 [樣品 › xxx.db ▼]  [開啟檔案] [重新整理] ☑量測中自動更新 │
├────────────────┬──────────────────────────┬─────────────────────────────────┤
│[1D 資料分析][2D]│ ☰ 資料            3/122 已選│ ∂ 微分 dV/dI · dI/dV   [13 筆][I→V]│
│                │ ⌕ [T_1.6K  104-116      ] │ 工具列                            │
│ 分析功能        │ run 量測  T   點數  描述    │                                  │
│  基本           │ 116 I→V 1.6 1001 ...      │          圖                       │
│  ∿ 曲線         │  9  V→I  -   201 ...      │  （滑鼠移上去顯示數值）             │
│  ∂ 微分         │──────────────────────────│                                  │
│  ⇅ 曲線+微分    │ ∂ 參數            [恢復預設]│                                  │
│  進階           │ 微分方法 [相鄰點差分 ▼]     │                                  │
│  Ω 電阻         │ ...                       ├─────────────────────────────────┤
│  ⟋ 線性化       │ 圖例 / 顏色 / 排序          │ ≡ 文字結果                        │
│  ◆ 轉折點       │ [▶ 繪圖][存圖][CSV][新視窗] │                                  │
└────────────────┴──────────────────────────┴─────────────────────────────────┘
```

- 左上切換 **1D / 2D**，整個介面的主色會跟著換（1D 藍、2D 紫），清單只顯示該類資料。
- 左欄是**功能卡片**，依「基本 / 進階」「色圖 / 萃取」分組。不支援目前所選資料的功能會變淡（例如選了 V→I 資料，轉折點分析就會變淡）。
- 中欄上方是 run 清單，「量測」欄顯示量測方式：`I→V`（藍）、`V→I`（綠）、`B⊥×I→V`（紫）。
- 開啟資料庫後會在背景把所有 run 讀進記憶體，之後切換、多選都很快。
- 量測進行中勾「自動更新」，新的 run 會自動出現（每 10 秒檢查一次）；也可以按 F5。
- 篩選欄：輸入文字（比對實驗描述與量測方式，例如 `T_1.6K`、`I+_38`、`V→I`），或 run 範圍 `104-116`；多個條件用空白分開。點欄位標題可以排序。
- 2D 色圖：**左鍵點色圖會在右邊加一條該位置的切線，右鍵清除**。
- 「存圖」「CSV」預設存到 `{.db 所在資料夾}\01 Figure\gui\`；「新視窗」會把目前的圖複製出來並排比較。
- **⚙ 設定**（右上角）：介面配色（5 組預設或自訂 HEX / RGB，重新開啟後套用）、預設的數據顏色循環、資料夾與輸出格式（寫回 `settings.toml`）、建立 / 修復捷徑、複製啟動檔到桌面。
- **數據顏色**：在 run 清單選取後按右鍵（或「● 顏色」按鈕）→ 用色盤選、輸入 HEX（`#e4572e`）或 RGB（`228, 87, 46`），也可以「依溫度套用兩色漸層」。設定過顏色的 run 在清單上會以該顏色顯示，存在樣品資料夾，下次開啟還在。
- **工具列的 Home（⌂）**：回到剛畫好時的範圍，並讓圖剛好填滿可見區域。
- **圖表樣式列**（圖的上方）：勾選格線、調整 X / Y 軸標籤、色條標籤、刻度、圖例、標題的字體大小。改了立刻套用、不重新計算，存圖也會照這個樣式，設定會記住。
- **逐筆設定**（例如線性化 dV/dI）：參數區下方會列出每一筆選取的 run，可以個別設定負側 / 正側 edge、平移、內插修補區間（例如 `-505:-495, 300:310`，把這些電流範圍的尖峰換成直線內插）。雙擊格子修改，清空 = 用預設；也可以直接在圖上拖曳 ▼ 標記（多筆）或藍 / 綠虛線（單筆）調整 edge。設定存在 `{.db 所在資料夾}\01 Figure\gui\per_run_settings.json`，下次開啟還在。

### 1D 功能

| 分組 | 功能 | 支援 |
|---|---|---|
| 基本 | 曲線（IV / VI） | 所有量測方式 |
| 基本 | 微分 dV/dI · dI/dV（相鄰點差分或 Savitzky-Golay，可 log、可取倒數） | 所有 |
| 基本 | 曲線 + 微分 上下對照 | 所有 |
| 進階 | 電阻 R（直線擬合）vs T | I→V、V→I |
| 進階 | 線性化 dV/dI（兩側直線取代雜訊；每筆可個別設定 edge、平移、修補區間，可在圖上拖曳） | I→V |
| 進階 | IV 轉折點 + Ic vs T（二次微分） | I→V |

### 2D 功能

| 分組 | 功能 | 支援 |
|---|---|---|
| 色圖 | 色圖（量測值）+ 點擊切線 | 任何 2D（慢軸可以是磁場、閘極…） |
| 色圖 | 色圖（微分 dZ/dF）+ 點擊切線 | 任何 2D |
| 萃取 | 切換電流 Ic vs 慢軸 | 快軸施加電流、量電壓 |
| 萃取 | 指定位置切線 + 轉折點（沒有剛好的位置就用最近的資料） | 同上 |

## 日常流程

| 想做的事 | `tools.bat` 選單 / 軟體 |
|---|---|
| 看資料、比較不同溫度／磁場、試參數 | 開啟軟體（開始功能表 / 桌面圖示 / `run_analysis.bat`） |
| 看某個 run（不開介面） | **2**（拖曳 .db 進視窗、輸入 run_id） |
| 查 run 編號 | **3** 列出 runs（可以用 `T_1.6K`、`By` 篩選） |
| 重新產生某個樣品的所有圖 | **4** → 選設定檔 → 完成後自動打開 `index.html` |
| 所有樣品全部重跑 | **5** |
| 要看滑鼠座標、放大縮小 | **6** 互動模式（開 matplotlib 視窗） |
| 新樣品 / 新的 .db | **7** 自動產生 `jobs\樣品名.toml`，再把裡面的數值改一改 |
| 找哪個 .db 有什麼量測 | **8** 掃描所有 .db → `catalog.xlsx`（有篩選功能；只重讀有變動的檔案） |

也可以把 `.toml` 直接拖到 bat 圖示上執行；把 `.db` 拖上去就是用分析介面開啟。

### 結果放在哪

預設在 `{.db 所在資料夾}\01 Figure\{設定檔名稱}\`，跟原本 notebook 的習慣一樣：

```
quad_002\01 Figure\quad_002\
├─ index.html            ← 所有分析的圖、數值、CSV 一頁看完
├─ _config_used.toml     ← 這次用的設定（日後可以對照）
├─ dVdI_vs_T_log\        ← 每個 job 一個資料夾
│   ├─ log_dVdI_temperature_overlay_lines.jpg
│   ├─ transitions.csv
│   └─ individual\       ← 各溫度的個別圖
└─ ...
```

每次重跑會覆蓋同名檔案，`index.html` 永遠是最新結果。想保留某一版，複製整個資料夾即可。

## 設定檔寫法（jobs\*.toml）

```toml
db = 'quad_002_2026-11-14_01.db'      # 完整路徑，或只寫檔名（自動在 data_root 底下找）

[defaults]                             # 所有 job 共用
voltage = "meas_voltage_K2"

[[job]]                                # 每個 [[job]] 是一個分析
name = "dVdI_vs_T_log"                 # 輸出資料夾名稱
type = "dvdi_vs_T"                     # 分析種類（見下表）
scale = "log"
edges = [[1.6, -870, 950], [2.8, -666, 718]]   # 依溫度內插

  [job.select]                         # 自動挑 run（放在 job 的最後面！）
  contacts = "I+_38, V+_39, V-_40, I-_41"
  T_range = [1.6, 2.8]
  duplicates = "latest"
```

**挑 run 的兩種方式**

- `runs = [116, 115, 114]`：直接指定。
- `[job.select]`：依條件自動挑，**之後新量的資料只要符合條件就會自動加進去**。可用的條件：

| 條件 | 說明 |
|---|---|
| `kind` | qcodes 實驗名稱，例如 `"IV"`、`"IV vs By"` |
| `contacts` | 接點，例如 `"I+_38, V+_39, V-_40, I-_41"`（從實驗描述自動解析） |
| `T_range` | 溫度範圍 `[1.6, 2.8]`（從描述的 `T_1.6K` 解析，也支援 `T_37mK`） |
| `run_range` | run 編號範圍 `[104, 116]` |
| `sweep` | `"I-->I+"`、`"I+->I-"`，或 `"none"` = 描述裡沒有標掃描方向的 |
| `field_axis` | `"y_field"` 等 |
| `name_contains` | 描述必須包含的文字（可以是 list） |
| `exclude` | 排除的 run，例如 `[103]` |
| `duplicates` | 同一溫度有多筆時：`"all"`（預設）、`"latest"`、`"first"` |

> ⚠️ TOML 的規則：`[job.select]` 下面的設定都會被算進 select，所以它要放在該 job 的**最後面**。放錯時工具會提醒你。

**依溫度變化的參數**：`edges`、轉折線的 `guess_uA`、`search_uA` 都可以寫成 `[[T, 值], ...]` 的表，表上沒有的溫度會自動線性內插，所以新加的溫度不必一一補參數。

其他共用選項：`labels = { "104" = "2.8 K (2nd)" }` 覆寫圖例；`temperatures = { "64" = 1.2 }` 補上描述裡沒有寫的溫度；`enabled = false` 暫時不跑；`xlim`、`ylim`。

## 分析種類與參數

括號內是預設值。

**`iv`**：IV 曲線
`x_unit`（"uA"／"mA"）、`individual`（true，存個別圖）、`overlay`（多筆時為 true）、`offset_mV`（0，疊圖平移）、`export_csv`（false）

**`dvdi`**：原始 dV/dI
`merge_duplicates`（true，同電流取平均）、`offset_ohm`（0）

**`linearize`**：兩側 Huber 直線取代雜訊，輸出 IV 比較圖、線性化 dV/dI、擬合參數 CSV（notebook 第 13、14 格）
`edges` 或 `edges_by_run = { "99" = [-666, 720] }`、`edge_width_uA`（500）、`show_raw_dvdi`（false）

**`dvdi_vs_T`**：線性化 dV/dI 溫度疊圖 + 轉折線追蹤 + I–T 相圖（notebook 第 15–17 格）
`scale`（"log"／"linear"）、`edges`、`offset_step`（log 2、linear 500）、`offsets`（個別覆寫）、`edge_width_uA`（500）、`smooth_window`（1）、`log_floor_ohm`（1e-6）、`downward_jump_decade`（0.25）、`median_window`（5）、`save_individual`（true）
轉折線：`[[job.transition_lines]]`（name、side、guess_uA、search_uA、min_step、window_uA、max_missing），以及 `transition_median_uA`（9）、`step_window_uA`（15）、`max_missing`（1）、`min_gap_uA`（20）、`manual_override = [["inner +", 108, 430.0]]`

**`iv_transitions`**：IV 二次微分找四個轉折點、Ic outer／inner vs T（notebook 第 18、19 格）
`initial_guess_mA`、`names`、`window_length`（15）、`poly_order`（3）、`first_half_width_mA`（0.12）、`step_in_mA`（0.20）、`step_out_mA`（0.05）、`min_gap_mA`（0.08）、`offset_step_mV`（0.5）
後處理：`unresolved_to_zero`（false）、`inner_zero_above_K`、`no_transition_T = [3.8]`

**`resistance`**：IV 直線擬合 R，畫 R vs T（原本的 `get_R_from_IV`）
`I_range_uA = [-100, 100]`（不寫 = 全部點）

**`field_map`**：V(B, I) 色圖（notebook 第 23–25 格）
`shift_B0`（false）、`B0_threshold_mV`（0.5）、`B0_search_mT`（例如 `[2, 6]`）、`cmap`（"magma"）、`vmin`／`vmax`、`field_index_range`、`field_label`

**`field_dvdi_map`**：dV/dI(B, I) 色圖（notebook 第 28 格）
`cmap`（"hot"）、`vmin`（0）、`vmax`（1）、`shift_B0`（false）

**`field_cuts`**：指定磁場切 IV，找 transition point 與 bulk transition（notebook 第 26、27 格）
`fields_mT = [...]` 或 `{ start = -1.5, stop = 2.1, step = 0.15 }`、`field_tolerance_mT`（"auto"：沒有剛好的磁場就用最近的資料；填數字則超過該距離才略過）、`shift_B0`（true）、`bulk`（true），以及 `baseline_max_uA`、`kink_threshold_mV`、`n_sigma`、`consecutive`、`jump_mV`、`kink_fit_points`、`bulk_threshold_mV`、`bulk_lookback`、`bulk_window_uA`、`bulk_min_step_mV`（預設值與 notebook 相同）

`python -m lta types` 可以列出所有分析種類。

## 在 Jupyter 裡用

選單 **J** 會開 Jupyter Lab。`Explore.ipynb` 示範了用法：

```python
from lta import QDB
db = QDB(r"...\quad_002_2026-11-14_01.db")
print(db.table())                                    # 像 qc.experiments()
c = db.load(116).curve()                             # 1D：c.x（施加）、c.y（量測），V→I 也適用
m = db.load(142).map2d()                             # 2D：m.slow / m.fast / m.z
runs = db.select(contacts="I+_38, V+_39, V-_40, I-_41", T_range=[1.6, 2.8], duplicates="latest")
```

最後一格示範怎麼直接呼叫介面上的分析功能畫圖，方便測試新寫的功能。

## 新增功能、新的量測方式

請看 **`擴充指南.md`**，裡面有：

- 程式架構與每個資料夾的用途
- 「想做的事 → 改哪裡」對照表（新的量測方式、新的 1D／2D 分析、3 維資料、換顏色）
- 量測模式怎麼判斷（看 qcodes 參數的單位：A→V 是 I→V、V→A 是 V→I）
- 完整範例：新增一個「零偏壓電導 G0 vs T」功能

最簡單的情況：複製 `lta\analysis1d\_template.py`（或 `analysis2d\_template.py`），改名、改函式，重新開介面就會出現在功能清單。

## 軟體模式的小提醒

- 程式出錯時會跳出提示，詳細記錄在 `%LOCALAPPDATA%\LowTempAnalysis\error.log`（`app.log` 是一般輸出）。
- 程式資料夾搬家後，重新雙擊一次 `install.bat`（或 ⚙ 設定 → 啟動與安裝 → 建立 / 修復捷徑）即可。
- 別台電腦開不起來：執行 `run_analysis.bat diag`，或看 `發佈與更新.md` 第 4 節。移除捷徑：選單或命令列執行 `python -m lta uninstall`（不會刪除程式與資料）。
- 程式仍是資料夾裡的 Python 原始檔，修改後重新開啟軟體就會生效，不需要重新安裝。

## 和原本 notebook 的差異

- 數值結果都對照過：轉折點表、Ic outer／inner、B0（run 142：7.80 mT，run 134：7.85 mT）、擬合參數都和 notebook 一致。notebook 裡 run 105、107、109、115 存的擬合輸出和現在程式碼重算的結果在小數點後第 2 位不同；用 notebook 原始程式碼直接重算也得到新工具的數字，可見那幾格的輸出是較早一次執行留下的。
- 原本 dV/dI 圖有幾處 x 軸標成 nA，實際單位是 μA，已修正。
- 最後一格（`R_DC1_d`）用到未定義的變數，沒有移植。
- `pyppt`（貼到 PowerPoint）沒有移植；結果統一存成圖檔與 `index.html`。
