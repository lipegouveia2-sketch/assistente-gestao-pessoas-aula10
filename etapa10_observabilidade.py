"""
ETAPA 10 -- Observabilidade (15 min): onde o tempo foi gasto e quanto custou, por nó do grafo.
"""
import functools
import json
import sys
import time

from dados_rh import SAIDAS

ARQUIVO_METRICAS = SAIDAS / "metricas.jsonl"


def limpar_metricas(thread_id: str) -> None:
    """Remove métricas anteriores do mesmo thread antes de iniciar uma nova execução."""
    if not ARQUIVO_METRICAS.exists():
        return
    linhas = [linha for linha in ARQUIVO_METRICAS.read_text(encoding="utf-8").splitlines() if linha.strip()]
    restantes = [linha for linha in linhas if json.loads(linha).get("thread_id") != thread_id]
    ARQUIVO_METRICAS.write_text("".join(f"{linha}\n" for linha in restantes), encoding="utf-8")


def medido(nome: str, no):
    # ETAPA 10.2 -- Wrapper para medir o tempo e registrar métricas do nó
    @functools.wraps(no)
    def wrapper(estado, *args, **kwargs):
        inicio = time.perf_counter()
        
        # Executa o nó original
        resultado = no(estado, *args, **kwargs)
        
        duracao = time.perf_counter() - inicio
        
        # Extrai informações opcionais se o resultado for um dicionário (comum em nós de grafos)
        tokens = None
        fonte_feriados = None
        if isinstance(resultado, dict):
            tokens = resultado.get("_metric_tokens")
            if tokens is None:
                tokens = resultado.get("tokens")
            if tokens is None:
                tokens = resultado.get("total_tokens")
            fonte_feriados = resultado.get("fonte_feriados")
            
        # Tenta pegar o thread_id do estado ou dos argumentos
        thread_id = None
        if isinstance(estado, dict):
            thread_id = estado.get("thread_id")
        
        # Monta o registro de métricas exigido
        metrica = {
            "thread_id": thread_id,
            "no": nome,
            "segundos": round(duracao, 4),
        }
        if tokens is not None:
            metrica["tokens"] = tokens
        if fonte_feriados is not None:
            metrica["fonte_feriados"] = fonte_feriados

        # Garante que o diretório de saída existe e grava a linha em JSONL
        ARQUIVO_METRICAS.parent.mkdir(parents=True, exist_ok=True)
        with open(ARQUIVO_METRICAS, "a", encoding="utf-8") as f:
            f.write(json.dumps(metrica, ensure_ascii=False) + "\n")
            
        if isinstance(resultado, dict) and "_metric_tokens" in resultado:
            resultado = {chave: valor for chave, valor in resultado.items() if chave != "_metric_tokens"}

        return resultado

    return wrapper


def resumo(thread_id: str) -> dict:
    """(PRONTO) Junta as linhas de ARQUIVO_METRICAS de uma execução. Só funciona depois que o medido() (10.2) grava."""
    if not ARQUIVO_METRICAS.exists():
        return {"erro": "saidas/metricas.jsonl ainda não existe: faça a ETAPA 10.2 e rode o grafo"}
    linhas = [json.loads(l) for l in ARQUIVO_METRICAS.read_text(encoding="utf-8").splitlines() if l.strip()]
    linhas = [l for l in linhas if l.get("thread_id") == thread_id]
    if not linhas:
        return {"erro": f"nenhuma métrica com thread_id={thread_id!r}"}
    lento = max(linhas, key=lambda l: l.get("segundos", 0))
    return {"nos": [l.get("no") for l in linhas],
            "segundos_total": round(sum(l.get("segundos", 0) for l in linhas), 2),
            "mais_lento": (lento.get("no"), lento.get("segundos")),
            "tokens_total": sum(l.get("tokens", 0) or 0 for l in linhas)}


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Uso: python etapa10_observabilidade.py <thread_id>")
        sys.exit(1)
    print(json.dumps(resumo(sys.argv[1]), ensure_ascii=False, indent=2))