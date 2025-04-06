import os
from langchain_community.embeddings import OpenAIEmbeddings
from langchain_community.vectorstores import FAISS
from langchain_community.document_loaders import PyMuPDFLoader, UnstructuredWordDocumentLoader, UnstructuredPowerPointLoader
from langchain_openai import ChatOpenAI, OpenAIEmbeddings  # questo sostituisce quello sopra
from langchain.text_splitter import RecursiveCharacterTextSplitter
from langchain.chains import RetrievalQA
from django.conf import settings

# === Config ===
os.environ["OPENAI_API_KEY"] = settings.OPENAI_API_KEY





# === Caricamento dei documenti PDF, Word, PowerPoint===
def load_all_documents(folder_path):
    documents = []
    for filename in os.listdir(folder_path):
        filepath = os.path.join(folder_path, filename)
        if filename.endswith(".pdf"):
            loader = PyMuPDFLoader(filepath)
        elif filename.endswith(".docx"):
            loader = UnstructuredWordDocumentLoader(filepath)
        elif filename.endswith(".pptx") or filename.endswith(".ppt"):
            loader = UnstructuredPowerPointLoader(filepath)
        else:
            continue
        documents.extend(loader.load())
    return documents




# === Pipeline RAG con salvataggio su FAISS ===
def create_rag_chain(docs):
    index_path = "vector_index"
    embeddings = OpenAIEmbeddings()

    if os.path.exists(index_path):
        print("🔁 Caricamento dell'indice FAISS esistente...")
        vectordb = FAISS.load_local(index_path, embeddings, allow_dangerous_deserialization=True)
    else:
        print("⚙️  Creazione nuovo indice FAISS...")
        from langchain.text_splitter import RecursiveCharacterTextSplitter
        splitter = RecursiveCharacterTextSplitter(chunk_size=500, chunk_overlap=50)
        split_docs = splitter.split_documents(docs)
        split_docs = [doc for doc in split_docs if doc.page_content.strip() != ""]

        vectordb = FAISS.from_documents(split_docs, embeddings)
        vectordb.save_local(index_path)
        print("💾 Indice FAISS salvato localmente")

    retriever = vectordb.as_retriever()
    qa = RetrievalQA.from_chain_type(llm=ChatOpenAI(temperature=0), retriever=retriever)
    return qa






# # === MAIN ===
# if __name__ == "__main__":
#     folder_path = "docs"
#     documents = load_all_documents(folder_path)
#     print(f"Caricati {len(documents)} documenti")
#
#     qa_chain = create_rag_chain(documents)
#
#     while True:
#         query = input("Fai una domanda sui documenti: ")
#         if query.lower() in ["exit", "quit"]:
#             break
#         risposta = qa_chain.invoke(query)
#         print(f"\nRisposta: {risposta}\n")
#
#
#
