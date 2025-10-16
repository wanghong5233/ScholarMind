# ScholarMind 🧠

> An AI-powered research assistant for academic literature, powered by advanced RAG technology.

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python Version](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/downloads/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.100%2B-009688.svg)](https://fastapi.tiangolo.com/)
[![React](https://img.shields.io/badge/React-18.0%2B-61DAFB.svg)](https://react.dev/)
[![RAG](https://img.shields.io/badge/RAG-Advanced-orange.svg)](#)
[![RL](https://img.shields.io/badge/Reranker-RL--Powered-red.svg)](#)
[![Agent](https://img.shields.io/badge/LLM-Agent-purple.svg)](#)

ScholarMind is an advanced research assistant designed for academic literature analysis. It goes beyond simple document Q&A by deeply understanding multi-modal academic papers, helping researchers with literature reviews, cross-paper comparison, and critical thinking.

## ✨ Core Features

- **🎨 Multi-modal Document Parsing**: Deep analysis of PDFs with text, tables, figures, and equations extraction
- **🎯 Advanced RAG Strategies**: 
  - Multi-Query retrieval with RRF fusion
  - Semantic-aware chunking for better context coherence
  - Metadata-enriched hierarchical retrieval
- **🔒 Private & Offline First**: Support for local LLM/Embedding models (no data leakage)
- **🔧 Pluggable Architecture**: Easily switch between different models (Embedders, Rerankers, LLMs)
- **📊 Scholar-Oriented Features**:
  - Cross-document comparison
  - Critical question generation
  - Citation tracking and visualization
- **📈 Production-Ready**: A/B testing framework, structured logging, and observability built-in

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

- **Backend**: FastAPI, Python 3.11+, SQLAlchemy, Alembic
- **Database**: PostgreSQL, Elasticsearch (vector store), Redis (cache)
- **Frontend**: React 18, TypeScript, Ant Design, Valtio
- **Core AI**: 
  - Embedding: bge-large-zh-v1.5 (local) / DashScope API
  - LLM: Qwen / GPT-4 / Local models
  - Parsing: deepdoc, PyMuPDF
- **DevOps**: Docker, Docker Compose

## 📖 Documentation

- **[Backend Architecture & API Reference](./backend/readme/readme.md)** - Detailed technical documentation with architecture diagrams
- **[Database Migrations Guide](./backend/app/alembic/README.md)** - Alembic migration instructions
- **[API Documentation](http://localhost:8000/docs)** - Interactive Swagger UI (after service startup)

## 🔬 Research & Innovation

This project explores cutting-edge techniques at the intersection of **RAG, RL, and LLM Agents**:

- **Reinforcement Learning for Information Retrieval**: Training rerankers on implicit user signals to maximize user satisfaction
- **LLM Agent Optimization**: Applying RL (PPO/REINFORCE) to improve multi-step planning and tool use in complex research tasks
- **Multimodal RAG**: Treating figures and tables as first-class knowledge units alongside text

These innovations aim to bridge the gap between academic research and production systems, demonstrating how advanced ML techniques can be applied to real-world problems.

## 🎯 Project Highlights

This project demonstrates production-level engineering practices in RAG systems:

### ✅ **Implemented Features**

1. **Semantic-Aware Chunking**: Splits documents based on sentence embedding similarity, preserving semantic coherence
2. **Hierarchical Retrieval Pipeline**: Multi-stage retrieval from broad recall (Multi-Query + RRF) to precise reranking
3. **Multimodal Information Extraction**: Extracts and indexes figure captions and table structures as first-class entities
4. **Context Compression**: Token-based conversation history management with rolling summaries
5. **Observability & A/B Testing**: Feature flags, JSONL event logging, and built-in experimentation framework
6. **Cross-Paper Comparison**: Structured comparison across multiple documents with citation tracking
7. **Critical Question Generation**: AI-powered question generation for deeper paper understanding

### 🚧 **Planned Features** (Roadmap)

#### **Phase 2: Advanced RAG Optimization**
- 🔄 **RL-Based Reranker**: Reinforcement learning model trained on implicit user feedback (citation clicks, dwell time) to optimize chunk ranking
  - **Target**: Improve Citation CTR by 20-30% and NDCG@5 by 10-15%
- 🔄 **Adaptive Retrieval Strategy**: Dynamic strategy selection based on query type and user context
- 🔄 **Query Understanding**: Intent classification and multi-hop question decomposition

#### **Phase 3: AI-Powered Writing Assistant**
- 📝 **Smart Citation Suggestion**: Cite-as-you-write functionality with automatic reference generation
- 📝 **Academic Language Polishing**: Transform drafts into publication-ready prose
- 📝 **Context-Aware Writing**: Generate paper sections grounded in your knowledge base

#### **Phase 4: Agentic Automation**
- 🤖 **Agent-Powered Related Work Generation**: Autonomous multi-step planning with tool calling
  - Tools: `search_papers`, `read_and_summarize`, `synthesize_content`, `format_bibliography`
  - **Target**: Generate 80% ready-to-use Related Work sections
- 🤖 **Agent Planning Optimization via RL**: Fine-tune agent decision-making policies using PPO/REINFORCE
  - Optimize tool selection and parameter generation for complex research tasks

#### **Phase 5: Knowledge Intelligence**
- 🕸️ **Knowledge Graph Construction**: LLM-powered entity and relation extraction
  - Visualize author networks, research trends, and paper relationships
- 📊 **Literature Analytics**: Topic modeling, citation impact analysis, research gap detection

## 🤝 Contributing

Contributions are welcome! Please check our `CONTRIBUTING.md` for guidelines.

## 📄 License

This project is licensed under the MIT License. See the `LICENSE` file for details.





