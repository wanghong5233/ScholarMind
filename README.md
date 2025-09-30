# ScholarMind 🧠

> An AI-powered research assistant for academic literature, based on multi-modal RAG technology.

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python Version](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/downloads/)

ScholarMind is not just a document Q&A tool. It aims to be an advanced research assistant that deeply understands multi-modal academic papers, helping researchers with literature reviews, idea inspiration, and paper drafting.

## ✨ Core Features

- **Multi-modal Document Parsing**: Deeply analyzes PDFs, extracting text, tables, and figures.
- **High-Precision Retrieval**: Utilizes advanced RAG strategies to ensure accurate information retrieval.
- **Private & Offline First**: Supports local deployment of embedding and LLM models, ensuring data privacy and cost efficiency.
- **Pluggable Architecture**: Easily switch between different models (Embedders, Rerankers, LLMs) through configuration.
- **Automated Literature Review (Coming Soon!)**: Agent-based automation for complex research tasks.
- **Fine-tuned Models for Academia (Roadmap)**: Plans to fine-tune models for specific academic domains.

## 🚀 Quick Start

This project is containerized using Docker. Ensure you have Docker and Docker Compose installed.

1.  **Clone the repository:**
    ```bash
    git clone https://github.com/your-username/ScholarMind.git
    cd ScholarMind
    ```

2.  **Configure Environment:**
    Navigate to the `backend` directory and set up your environment variables.
    ```bash
    cd backend
    cp .env.example .env
    ```
    Now, edit the `.env` file to add your API keys or modify configurations as needed.

3.  **Launch Services:**
    From the `backend` directory, run:
    ```bash
    docker-compose up -d --build
    ```
    This will build the necessary images and start all services (API, database, vector store) in the background.

4.  **Access the API:**
    Once the services are up, you can access the API documentation at [http://localhost:8000/docs](http://localhost:8000/docs).

## 🛠️ Tech Stack

- **Backend**: FastAPI, Python
- **Database**: PostgreSQL, Elasticsearch, Redis
- **Frontend**: React, Ant Design
- **Core AI**: SentenceTransformers, PyTorch, Hugging Face

## 🗺️ Roadmap

We are just getting started! The development plan is tracked in our [Action Outline](./行动纲领.md).

- [ ] **Phase 0: Purification & Rebranding** - ✅
- [ ] **Phase 1: Architectural Refactoring**
    - [ ] Implement Dependency Injection.
    - [ ] Deploy private, local models for Embedding & Reranking.
    - [ ] Centralize all configurations.
- [ ] **Phase 2: Scholar-Oriented Features**
- [ ] **Phase 3: Building the Moat**

## 🤝 Contributing

Contributions are welcome! Please check our `CONTRIBUTING.md` for guidelines.

## 📄 License

This project is licensed under the MIT License. See the `LICENSE` file for details.





