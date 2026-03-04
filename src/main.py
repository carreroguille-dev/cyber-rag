import argparse
import asyncio
import sys

from dotenv import load_dotenv

load_dotenv()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="RAG Agéntico — Guía Nacional de Ciberincidentes"
    )
    parser.add_argument(
        "--ingest",
        action="store_true",
        help="Ejecuta la ingesta del PDF en Qdrant",
    )
    parser.add_argument(
        "--version",
        action="store_true",
        help="Muestra la versión del sistema",
    )
    args = parser.parse_args()

    if args.version:
        print("RAG Ciberincidentes v1.0 — Guía Nacional 2020")
        sys.exit(0)

    if args.ingest:
        from src.ingestion.indexer import main as indexer_main
        asyncio.run(indexer_main())
        sys.exit(0)

    from src.ui.app import construir_interfaz
    app = construir_interfaz()
    app.launch(
        server_name="0.0.0.0",
        server_port=7860,
        show_api=False,
    )


if __name__ == "__main__":
    main()
