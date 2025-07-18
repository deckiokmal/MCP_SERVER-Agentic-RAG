from pathlib import Path
from typing import List, Dict, Tuple, Optional, Any

from docling.document_converter import DocumentConverter

from mcp_server.utils.rag_pipeline import RAGPipeline
from mcp_server.utils.logger import logger
from mcp_server.settings import Settings


class RAGTools:
    def __init__(self):
        """
        Inisialisasi RAGTools dengan membuat instance RAGPipeline.
        """
        self.settings = Settings()  # type: ignore
        self.pipeline = RAGPipeline()

    def add_product_knowledge(
        self,
        base_dir: str | None = None,
        project_name: str = "product_standard",
        tahun: str = "2025",
    ) -> None:
        """
        Mengunggah dan mengindeks seluruh file PDF di direktori product_standard
        sebagai knowledge produk untuk RAG dengan metadata project dan tahun.

        Args:
            base_dir (str): Path ke direktori PDF product_standard.
            project_name (str): Nama project metadata.
            tahun (str): Tahun metadata.
        """
        if base_dir is None:
            base_dir = self.settings.knowledge_base_path

        path = Path(base_dir)
        pdf_files = list(path.glob("*.pdf"))
        if not pdf_files:
            logger.warning(f"Tidak ada PDF di {base_dir}.")
            return

        entries = []
        for pdf in pdf_files:
            try:
                result = DocumentConverter().convert(source=str(pdf))
                chunks = self.pipeline._chunk_document(
                    result.document, pdf.name, project_name, tahun
                )
                entries.extend(chunks)
                logger.info(f"'{pdf.name}' siap diindeks sebagai product knowledge.")
            except Exception as e:
                logger.error(f"Gagal proses '{pdf.name}': {e}")

        if entries:
            self.pipeline.table.add(entries)
            logger.info(
                f"{len(entries)} chunk product knowledge ditambahkan ke vectorstore."
            )
        else:
            logger.info("Tidak ada chunk product knowledge yang ditambahkan.")

    def add_kak_tor_knowledge(
        self,
        base_dir: str | None = None,
        md_dir: str | None = None,
        project: Optional[str] = None,
        tahun: Optional[str] = None,
    ) -> None:
        """
        Mengunggah PDF KAK/TOR, mengekspor semua ke Markdown, dan
        mengindeksnya ke RAG dengan metadata user.

        Args:
            base_dir (str): Path ke direktori PDF KAK/TOR.
            md_dir (str): Path ke direktori penyimpanan Markdown hasil ekspor.
            project (Optional[str]): Metadata nama project (manual).
            tahun (Optional[str]): Metadata tahun (manual).
        """
        if base_dir is None:
            base_dir = self.settings.kak_tor_base_path
        pdf_path = Path(base_dir)

        if md_dir is None:
            md_dir = self.settings.kak_tor_md_base_path

        md_path = Path(md_dir)
        md_path.mkdir(parents=True, exist_ok=True)

        pdf_files = list(pdf_path.glob("*.pdf"))
        if not pdf_files:
            logger.warning(f"Tidak ada PDF di {base_dir}.")
            return

        entries = []
        for pdf in pdf_files:
            try:
                # Konversi ke markdown
                result = DocumentConverter().convert(source=str(pdf))
                md_content = result.document.export_to_markdown()

                # Simpan markdown
                out_md = md_path / f"{pdf.stem}.md"
                out_md.write_text(md_content, encoding="utf-8")
                logger.info(f"'{pdf.name}' diekspor ke Markdown: {out_md}")

                # Chunk dan indeks Markdown
                chunks = self.pipeline._chunk_document(
                    result.document, pdf.name, project or "kak_tor", tahun or "2025"
                )
                entries.extend(chunks)
            except Exception as e:
                logger.error(f"Gagal proses KAK/TOR '{pdf.name}': {e}")

        if entries:
            self.pipeline.table.add(entries)
            logger.info(f"{len(entries)} chunk KAK/TOR ditambahkan ke vectorstore.")
        else:
            logger.info("Tidak ada chunk KAK/TOR yang ditambahkan.")

    def add_kak_tor_summaries_knowledge(
        self,
        markdown_name: Optional[str] = None,
        project: str = "default",
        tahun: str = "2025",
    ) -> None:
        """
        Indeks dokumen Markdown KAK/TOR langsung ke vectorstore tanpa ekspor file.

        Args:
            markdown_name (str, optional): Nama file .md.
            project (str): Metadata project.
            tahun (str): Metadata tahun.
        """
        base = Path(self.settings.summaries_md_base_path)
        # Daftar semua .md yang tersedia
        available_files = [p.name for p in base.glob("*.md")]

        if not markdown_name:
            logger.warning(
                "Tidak ada nama file yang diberikan. File .md yang tersedia: "
                f"{available_files}"
            )
            return

        name_stem = markdown_name.strip()
        # Kandidat: exact, normalized, contains
        candidates = [
            base / f"{name_stem}.md",
            base / f"{name_stem.lower().replace(' ', '_')}.md",
        ]
        # fallback: any file yang stem-nya mengandung name_stem (case-insensitive)
        candidates += [
            p for p in base.glob("*.md") if name_stem.lower() in p.stem.lower()
        ]

        # Pilih file pertama yang ada
        file_path = next((p for p in candidates if p.exists()), None)

        if not file_path:
            logger.warning(
                f"File '{markdown_name}.md' tidak ditemukan.\n"
                f"File .md yang tersedia di {base}: {available_files}"
            )
            return

        # Proses hanya file_path
        try:
            # Convert markdown ke dokumen dan chunk
            result = DocumentConverter().convert(source=str(file_path))
            chunks = self.pipeline._chunk_document(
                result.document, file_path.name, project, tahun
            )
            if chunks:
                self.pipeline.table.add(chunks)
                logger.info(
                    f"{len(chunks)} chunk dari '{file_path.name}' berhasil diindeks."
                )
            else:
                logger.warning(
                    f"Tidak ada chunk yang dihasilkan dari '{file_path.name}'."
                )
        except Exception as e:
            logger.error(f"Gagal memproses Markdown '{file_path.name}': {e}")

    def build_instruction_context(
        self,
        template_name: str,
        kak_md_dir: str | None = None,
        selected_files: Optional[List[str]] = None,
    ) -> Tuple[str, str]:
        """
        Menggabungkan konten Markdown KAK/TOR yang dipilih dengan prompt template,
        mengembalikan instruksi dan context.

        Args:
            template_name (str): Nama file template di direktori templates.
            kak_md_dir (str): Direktori Markdown KAK/TOR.
            selected_files (Optional[List[str]]): Daftar nama file Markdown untuk digabung.

        Returns:
            Tuple[str, str]: (instruksi, context) untuk digunakan LLM.
        """
        # Baca template
        tmpl_path = Path(self.settings.templates_base_path) / f"{template_name}.txt"
        template = tmpl_path.read_text(encoding="utf-8")

        # Kumpulkan konten markdown
        if kak_md_dir is None:
            kak_md_dir = self.settings.kak_tor_md_base_path
        md_base = Path(kak_md_dir)
        if selected_files:
            files = [md_base / f for f in selected_files]
        else:
            files = list(md_base.glob("*.md"))

        contexts = []
        for md in files:
            text = md.read_text(encoding="utf-8")
            contexts.append(f"---\n# {md.name}\n{text}\n")

        # Gabungkan
        combined_context = "\n".join(contexts)

        instruksi = template
        return instruksi, combined_context

    def build_summary_tender_payload(
        self,
        prompt_instruction_name: str,
        kak_tor_name: Optional[str] = None,
    ) -> Dict[str, str]:
        """
        Mengembalikan dict:
            {
              "instruction": <isi prompt instruction>,
              "context":     <konten markdown KAK/TOR>
            }

        Args:
            prompt_instruction_name : nama file template .txt (tanpa ekstensi)
            kak_tor_name            : nama *satu* file .md KAK/TOR

        Raises:
            ValueError      : jika kak_tor_name tidak diberikan
            FileNotFoundError: jika template atau file .md tidak ditemukan
        """
        # 1. ambil template .txt
        tmpl_path = (
            Path(self.settings.templates_base_path) / f"{prompt_instruction_name}.txt"
        )
        if not tmpl_path.is_file():
            raise FileNotFoundError(
                f"Template instruction tidak ditemukan: {tmpl_path}"
            )
        instruction = tmpl_path.read_text(encoding="utf-8")

        # 2. direktori default KAK/TOR (tidak berubah-ubah)
        md_base: Path = Path(self.settings.kak_tor_md_base_path)

        # 3. validasi kak_tor_name
        if kak_tor_name is None:
            available = [p.name for p in md_base.glob("*.md")]
            raise ValueError(
                "Parameter 'kak_tor_name' wajib diisi.\n"
                f"Daftar file KAK/TOR yang tersedia: {available}"
            )

        md_path: Path = md_base / kak_tor_name
        if not md_path.is_file():
            available = [p.name for p in md_base.glob("*.md")]
            raise FileNotFoundError(
                f"File KAK/TOR '{kak_tor_name}' tidak ditemukan.\n"
                f"Pastikan nama benar. Pilihan yang tersedia: {available}"
            )

        # 4. baca markdown
        md_text = md_path.read_text(encoding="utf-8")
        context = f"---\n# {md_path.name}\n{md_text}\n"

        return {"instruction": instruction, "context": context}

    def retrieval_with_filter(
        self,
        query: str,
        k: Optional[int] = None,
        metadata_filter: Optional[Dict[str, Any]] = None,
    ) -> str:
        """
        Melakukan retrieval menggunakan RAGPipeline dengan filter metadata.

        Args:
            query (str): Teks pertanyaan.
            k (Optional[int]): Jumlah hasil teratas.
            metadata_filter (Optional[Dict[str, Any]]): Dict filter metadata.

        Returns:
            str: Hasil pencarian dengan citation.
        """
        return self.pipeline.retrieval(
            query=query, k=k, metadata_filter=metadata_filter
        )

    def reset_knowledge_base(self) -> None:
        """
        Hapus dan buat ulang seluruh vectorstore.
        """
        self.pipeline.reset_vectorstore()

    def update_chunk_metadata(
        self,
        metadata_filter: Dict[str, Any],
        new_metadata: Dict[str, Any],
    ) -> int:
        """
        Update metadata untuk semua chunk yang sesuai filter.

        Args:
            metadata_filter (Dict[str, Any]): Filter untuk menemukan chunk, contoh {"project":"Alpha"}.
            new_metadata (Dict[str, Any]): Metadata baru untuk di-merge, contoh {"tahun":"2024"}.

        Returns:
            int: Jumlah chunk yang diupdate.
        """
        # Build filter expression
        clauses = []
        for k, v in metadata_filter.items():
            val = f"'{v}'" if isinstance(v, str) else v
            clauses.append(f"metadata.{k} = {val}")
        filter_expr = " AND ".join(clauses)

        # Ambil semua chunk yang sesuai
        df = (
            self.pipeline.table.search([0] * self.pipeline.vector_dim)
            .where(filter_expr)
            .limit(None)
            .to_pandas()
        )
        if df.empty:
            return 0

        # Hapus chunk lama dan siapkan entry baru dengan metadata terbarukan
        try:
            self.pipeline.table.delete(filter_expr)
        except Exception:
            logger.warning(
                "Tidak dapat menghapus chunk lama via filter, coba lanjutkan overwrite."
            )

        updated_entries = []
        for _, row in df.iterrows():
            merged_meta = {**row["metadata"], **new_metadata}
            updated_entries.append(
                {
                    "text": row["text"],
                    "vector": row["vector"],
                    "metadata": merged_meta,
                }
            )

        self.pipeline.table.add(updated_entries)
        return len(updated_entries)

    def get_vectorstore_stats(self) -> Dict[str, Any]:
        """
        Mengembalikan statistik vectorstore: total rows, ukuran (MB), daftar project unik, distribusi tahun.
        """
        total = self.pipeline.table.count_rows()
        # Hitung ukuran folder persist_dir
        base = Path(self.pipeline.persist_dir)
        size_bytes = sum(f.stat().st_size for f in base.rglob("*") if f.is_file())
        size_mb = size_bytes / (1024 * 1024)
        # Ambil metadata untuk analisis
        df = self.pipeline.table.to_pandas()
        projects = (
            df["metadata"].apply(lambda m: m.get("project")).dropna().unique().tolist()
        )
        tahun_dist = (
            df["metadata"]
            .apply(lambda m: m.get("tahun"))
            .dropna()
            .value_counts()
            .to_dict()
        )
        return {
            "total_rows": total,
            "size_mb": round(size_mb, 2),
            "projects": projects,
            "tahun_distribution": tahun_dist,
        }

    def rebuild_all_embeddings(self, batch_size: int = 100) -> None:
        """
        Re-embed semua chunk dengan model embedding saat ini dan perbarui vectorstore.

        Args:
            batch_size (int): Jumlah chunk yang diproses per batch.
        """
        df = self.pipeline.table.to_pandas()
        entries = []
        for _, row in df.iterrows():
            vec = self.pipeline.embed.embed_query(row["text"])
            self.pipeline._validate_vector_dim(vec)
            entries.append(
                {
                    "text": row["text"],
                    "vector": list(vec),
                    "metadata": row["metadata"],
                }
            )
        # Reset and re-add
        self.pipeline.reset_vectorstore()
        self.pipeline.table.add(entries)
        logger.info(f"Rebuild embeddings selesai: {len(entries)} chunk diperbarui.")

    def list_metadata_values(self, field: str) -> List[Any]:
        """
        Mengembalikan daftar unik nilai untuk metadata key tertentu.

        Args:
            field (str): Nama field metadata, misal 'project' atau 'tahun'.

        Returns:
            List[Any]: Daftar unik nilai.
        """
        df = self.pipeline.table.to_pandas()
        values = df["metadata"].apply(lambda m: m.get(field)).dropna().unique().tolist()
        return values
