# TransGemini

**TransGemini** is a Python-based desktop application for translating text documents (EPUB, DOCX, TXT, HTML, and more) using Google's Gemini models via [google-generativeai](https://pypi.org/project/google-generativeai/). The app includes a full graphical interface built with PyQt6.

## 🧠 Features

- Load and parse `.epub`, `.docx`, `.txt`, `.html`, and `.fb2` files  
- Extract text and images  
- Translate content using Gemini 1.5 or 2.5 (selectable model)  
- Edit translation prompt within the UI  
- Export to multiple formats: `.txt`, `.docx`, `.epub`, `.html`, `.md`, `.fb2`  
- Automatic chunking for long texts  
- Image placeholder insertion and reintegration  
- EPUB rebuild support  

## 🛠 Requirements

- Python `3.10.11`  
- All dependencies are listed in [`requirements.txt`](./requirements.txt)  

Install them with:

```bash
pip install -r requirements.txt
```

---

## 📡 API Key Setup (Google Gemini)

In order to use the Gemini translation feature, you must create an API key:

### 🔑 How to get your Gemini API key

1. Visit [https://aistudio.google.com/apikey](https://aistudio.google.com/apikey)
2. Log in with your Google account (if not already)
3. Click **"Create API Key"**
4. Copy the generated key

### 📥 How to use it

1. Launch the app using:

```bash
python transgemini/main.py
```

2. In the GUI, you will be prompted to paste your API key (or you can enter it manually in settings)
3. The key will be saved in `translator_settings.ini` locally for future use

> 💡 Your API key should be kept private. Avoid sharing it in screenshots, code, or public repositories.

---

## 📦 Project Structure

```
transgemini/
├── main.py                # Entry point  
├── old_main.py            # Legacy monolithic version (5K+ lines, preserved for reference)  
├── config.py              # Constants and settings  
├── core/                  # Core logic (parsing, translation, EPUB)  
├── ui/                    # PyQt6 interface  
├── requirements.txt  
└── README.md  
```

## 🚀 How to Run

```bash
python transgemini/main.py
```

The app will launch a PyQt6 GUI.

---

## 🤝 How to Contribute

We welcome contributors! Here's how to do it right:

### 1. **Fork the repository**
Click the `Fork` button on GitHub.

### 2. **Clone your fork**

```bash
git clone https://github.com/your-username/transgemini.git
cd transgemini
```

### 3. **Add the original repo as upstream (once)**

```bash
git remote add upstream https://github.com/original-author/transgemini.git
```

### 4. **Create a new branch**

```bash
git checkout -b feature/my-feature-name
```

### 5. **Make changes and commit**

```bash
git add .
git commit -m "Add: short description of what was changed"
```

### 6. **Sync with upstream before pushing**

If `main` branch was updated:

```bash
git fetch upstream
git rebase upstream/main
```

💡 *Why rebase?*  
It keeps your history clean and avoids merge commits. You fix conflicts locally, so the pull request stays readable.

If you're not confident with rebase, you can also use:

```bash
git merge upstream/main
```

But **prefer `rebase`** unless you’re merging a big feature branch.

### 7. **Push your branch**

```bash
git push origin feature/my-feature-name
```

### 8. **Open a Pull Request**
- Go to your fork on GitHub  
- Click **"Compare & Pull Request"**  
- Provide a meaningful title and description  

We'll review and merge it!

---

## 📝 Prompt Customization

In the GUI, you can customize the prompt used for Gemini translation. This allows you to fine-tune translation style or add contextual instructions (e.g., "translate to Russian in literary style").

---

## 🛡 License

This project is open-source. Licensing info TBD by the original author.

---

## ❤️ Acknowledgements

- [Google Generative AI](https://ai.google.dev/)  
- [BeautifulSoup](https://www.crummy.com/software/BeautifulSoup/)  
- [ebooklib](https://github.com/aerkalov/ebooklib)  
- [python-docx](https://github.com/python-openxml/python-docx)  
- [PyQt6](https://www.riverbankcomputing.com/software/pyqt/)

---

_This README was written with love and markdown._