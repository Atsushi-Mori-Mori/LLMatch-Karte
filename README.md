# プロジェクト(Project)
　本プロジェクトは松尾研LLM CommunityにおけるLLMatchでの電子カルテプロジェクトの
取組みの一つである。希少ウィルス感染の自由形式のカルテからFHIR形式の構造化電子カルテを
作成することが目的である。日本ではほとんど感染例のないアレナウィルス等による感染症を扱う。
本GithubではMask-Filling手法による合成データの試行について記載している。
Mask-Filling手法については関連リンクに論文を示している。<br>

<img src="docs/images/clinicalAI.jpg" alt="電子カルテ" width="480">

## プログラム及びデータ概要(Program and Data Overview)
<11_入力ファイル><br>
karte_example.csv：PubMDから抽出の症例5サンプル<br>
※フルサンプルのファイルは石田さんに依頼ください。<br>
<21_バッチ処理関連ファイル(1)><br>
run_mask_trial3.bat：Mask化バッチ処理Trial3<br>
run_mask_filling_trial4.bat：Filling化バッチ処理Trial4<br>
<22_Jupyterノートブック処理関連ファイル(2)><br>
mask-trial3.ipynb：Mask化ノートブックTrial3<br>
mask-filling-trial4.ipynb：Filling化ノートブックTrial4<br>
<23_ソースプログラムファイル><br>
mask_trial2.py：Mask化処理拡張版<br>
mask_trial3.py：Trial2+医学的誤Mask防止版<br>
mask_filling_trial3.py：制約付きFillingの基盤<br>
mask_filling_trial4.py：Trial3に安全性filterと品質検証を追加<br>
<31_後処理プログラムファイル><br>
Dkt01.py：Mask化出力ファイルからMask化カテゴリ数算出<br>
Dkt11.py：Filling化出力ファイルから必要カラム抽出<br>

## フォルダ構成(Folder Structure)
　フォルダ構成を以下に示す。(1)バッチ処理時はローカルPC上の構成を示す。
(2)ノートブック処理時はGoogle Driveにマウント後の構成を示す。<br>
<details>
  <summary>ディレクトリ構成を開く</summary>
MyDrive/<br>
   └─ LLMatch2026/<br>
      ├─ mask-trial3.ipynb<br>
      ├─ mask-filling_trial4.ipynb<br>
      ├─ src/<br>
      │  ├─ mask_trial2.py<br>
      │  ├─ mask_trial3.py<br>
      │  ├─ mask_filling_trial3.py<br>
      │  └─ mask_filling_trial4.py<br>
      ├─ example/<br>
      │  └─ karte_example.csv<br>
      ├─ mask_trial_outputs/<br>
      └─ mask_filling_outputs/<br>
</details>

## 動作環境(Execution Environment)
(1)バッチ処理<br>
- Windows / Python(Anaconda等)など<br>

(2)ノートブック処理<br>
- Google Colabo (with GPU)<br>

## 基本的な使い方(Basic Usage)
(1)バッチ処理<br>
ローカルPCで実行する場合はPython実行環境の構築が必要です。一方、GPU付きPCは
処理自体には必須ではありません。ただし、現在のバッチファイルはGPU使用を前提
にしています。<br>
### 環境構築<br>
- コマンドプロンプトを開く<br>
- WSLが利用できるか確認する<br>
　WSLがインストールされていない場合は、 wsl --install -d Ubuntu<br>
- GPUがWSLから認識されるか確認する<br>
- 必要な基本コマンドをインストールする<br>
　sudo apt update<br>
　sudo apt install -y curl ca-certificates<br>
- uvがインストールされているか確認する<br>
- Python 3.12をインストールする<br>
- maskfill_env 仮想環境を作成する<br>
- pipを更新する<br>
- CUDA対応PyTorchをインストールする<br>
- GPUがPyTorchから認識されるか確認する<br>
- Pythonファイルの構文を確認する<br>
- WSLを終了する<br>
- リポジトリへ移動する<br>
### Mask化処理実行<br>
- コマンドプロンプトを開く<br>
- プロジェクトフォルダへ移動する<br>
　cd /d C:\LLMatch2026<br>
- 入力CSVが存在することを確認する<br>
- 症例をMask化する<br>
　run_mask_trial3.bat example/karte_example.csv mask_trial_outputs<br>
　※3番目に引数で数字を付けると処理する症例数を示す。<br>
- 正常終了メッセージを確認する<br>
　CSV saved: mask_trial_outputs/clinical_case_mask_trial3_YYYYMMDD_HHMMSS.csv<br>
　TXT saved: mask_trial_outputs/clinical_case_mask_trial3_YYYYMMDD_HHMMSS.txt<br>
### Filling化処理実行<br>
- コマンドプロンプトを開く<br>
- プロジェクトフォルダへ移動する<br>
　cd /d C:\LLMatch2026<br>
- Mask化CSVを確認する<br>
- Filling処理を実行する<br>
　run_mask_filling_trial4.bat "" mask_filling_outputs 3 10 10<br>
  第1引数：入力CSV。""の場合は最新のMask化CSV<br>
  第2引数：出力フォルダ<br>
  第3引数：処理する入力症例数<br>
  第4引数：1症例当たりの生成数<br>
  第5引数：TOP_K<br>
- 実行設定を確認する<br>
  処理開始時に次のような表示が出ます。<br>
  [INFO] Input CSV           : mask_trial_outputs/clinical_case_mask_trial3_YYYYMMDD_HHMMSS.csv<br>
  [INFO] Output dir          : mask_filling_outputs<br>
  [INFO] Augmentation factor : 10<br>
  [INFO] Top K               : 10<br>
  [INFO] Max rows            : 3<br>

(2)ノートブック処理<br>
Google Drive上に上記構成を作成しノートブックを開いて上から順番に実行。<br>

## 出力ファイルと保存先(Output Files and Storage)
Mask化処理とFilling化処理で異なるフォルダに結果ファイルが作成される。<br>
<Mask化出力ファイル:mask_trial_outputs><br>
clinical_case_mask_trial3_YYYYMMDD_HHMMSS.csv: Mask化出力ファイル<br>
<Mask化出力ファイル:mask_filling_outputs><br>
clinical_case_mask_filling4_YYYYMMDD_HHMMSS.csv: Filling化出力ファイル<br>

## No.3 マスク手法に依るデータ増強
　SYNTHETIC4HEALTH: generating annotated synthetic clinical letters<br>
### 処理フロー概要<br>
- 前処理と特徴量抽出<br>
　マスクをかける前に、まず「何を隠し、何を残すべきか」を判断するための解析<br>
　　・構造の抽出<br>
　　・個人情報の特定<br>
　　・医学用語･エンティティの認識<br>
　　・品詞(POS)タグ付け<br>
- マスク処理の実行<br>
　特徴抽出の結果に基づき、以下の戦略でテキストの一部を <mask> トークンに置換<br>
　　・ランダムマスク<br>
　　・品詞ベースマスク<br>
　　・ストップワードマスク<br>
- 言語モデルによる穴埋め生成<br>
　マスクされたテキスト（Masked Letters）を言語モデル（Bio_ClinicalBERT等）に入力<br>
　　・単語予測による<mask>部分の埋め合わせ<br>
　　・症例の臨床的事実を維持しつつ新合成カルテ生成<br>
- 後処理<br>
　　・匿名化個所の空白の充填<br>
　　・誤記等のスペル修正で品質向上<br>

<img src="docs/images/Flow01.jpg" alt="処理フロー" width="480">

### 使用モデル
- MLM(Masked Language Model)モデル(Bio_ClinicalBERT等)
　Mask-fillingの中心処理のモデル
- 生成AI(BioGPT, GPT-3.5-Turbo等)
　評価(LLM-as-a-Judge)モデル

### 将来のマスク処理向けモデル
- Mask-filling向けモデルとして以下のモデルが検討されている。
　・CLM(Causal Language Model: 因果的言語モデル)モデル
　・ローカルLLM

## 関連リンク(Related Links)
Mask-Filling手法による合成データ作成<br>
SYNTHETIC4HEALTH: generating annotated synthetic clinical letters<br>
https://www.frontiersin.org/journals/digital-health/articles/10.3389/fdgth.2025.1497130/full<br>
Generation and Evaluation of Realistic Synthetic Clinical Progress Notes for Prostate Cancer using Large Language Models<br>
https://www.medrxiv.org/content/10.64898/2026.05.25.26354027v1.full<br>
Are synthetic clinical notes useful for real natural language processing tasks: A case study on clinical entity recognition<br>
https://pmc.ncbi.nlm.nih.gov/articles/PMC8449609/<br>
Generating Synthetic Free-text Medical Records with Low Re-identification Risk using Masked Language Modeling<br>
https://aclanthology.org/2025.naacl-srw.20.pdf<br>

## 注意事項(Notes)
None


