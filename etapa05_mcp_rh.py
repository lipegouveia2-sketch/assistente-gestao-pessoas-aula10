"""
ETAPA 5 -- MCP Server do RH (30 min). Volta aqui na ETAPA 9 (token, regra N8 e auditoria).

Os dados saem de dentro do agente e passam a morar num PROCESSO SEPARADO, que fala MCP. O agente (Etapa 5.3) e os
nós do grafo (Etapa 7, via cliente_mcp.chamar_mcp) só enxergam as tools abaixo: nunca os CSVs.

ESQUELETO: implemente as etapas na ordem (troque cada raise NotImplementedError pelo código da etapa).
  ETAPA 5.1 -- as 5 tools de LEITURA: declare cada uma com @mcp.tool(), escreva a DOCSTRING (é o "schema" que o LLM
               lê) e devolva o resultado da consulta pronta de dados_rh.py. Toda tool devolve UM dict.
  ETAPA 5.2 -- a tool de ESCRITA registrar_decisao, IDEMPOTENTE (protocolo repetido não duplica)
  ETAPA 9.2 -- autorização NO SERVER: token + regra N8 (chefia certa, nunca o próprio servidor) + decisão num enum
  ETAPA 9.3 -- auditoria: toda tentativa de escrita (ALLOW ou DENY) vira uma linha em saidas/auditoria.jsonl

REFERÊNCIAS NAS AULAS
  aula6/desafio2/mcp_unidades.py               MCP Server que lê um CSV                          <- o mais parecido (5.1)
  aula5/exemplos/03_mcp_parametros/server.py   tool com parâmetros opcionais
  aula5/exemplos/05_multiplas_tools/server.py  várias tools no mesmo server, tool de "descoberta"
  aula3/agente_retry3.py                       idempotência (5.2)
  aula5/exemplos/10_seguranca/server_seguro.py token, enum fechado e auditoria NO SERVER        <- o mais parecido (9.2/9.3)

Testar sem agente (MCP Inspector, precisa de Node.js):
    npx @modelcontextprotocol/inspector python etapa05_mcp_rh.py

Pronto quando: o Inspector lista as 6 tools; registrar C1 duas vezes gera um único registro.
"""
import json
import os
from datetime import datetime

from dotenv import load_dotenv
from mcp.server.mcpserver import MCPServer

from dados_rh import PASTA, SAIDAS, carregar_servidores, diaria, escala_da_equipe, normas, remuneracao, servidor_publico

# O server roda como SUBPROCESSO: ele não herda o ambiente de quem o chamou. Por isso carrega o .env sozinho
# (mesmo cuidado de aula5/exemplos/02_mcp_basico/database.py).
load_dotenv(PASTA / ".env")

mcp = MCPServer("rh-mcp")

ARQUIVO_REGISTROS = SAIDAS / "registros.json"
ARQUIVO_AUDITORIA = SAIDAS / "auditoria.jsonl"
TOKEN_REGISTRO = os.getenv("TOKEN_REGISTRO", "")
DECISOES_VALIDAS = {"deferido", "indeferido", "indeferido_pela_chefia"}
TIPOS_COM_CHEFIA = {"ferias", "diaria"}  # regra N8


def _ler_registros() -> dict:
    """(PRONTO) {protocolo: registro} gravados até agora."""
    if ARQUIVO_REGISTROS.exists():
        return json.loads(ARQUIVO_REGISTROS.read_text(encoding="utf-8"))
    return {}


def _gravar_registros(registros: dict) -> None:
    """(PRONTO)"""
    ARQUIVO_REGISTROS.write_text(json.dumps(registros, ensure_ascii=False, indent=2), encoding="utf-8")


# ------------------------------------------------------------------ ETAPA 5.1: leitura
@mcp.tool()
def consultar_servidor(matricula: str) -> dict:
    """Cadastro de UM servidor: nome, cargo, equipe, saldo de férias, abonos restantes e chefia imediata.
    NÃO inclui o salário (dado restrito). Se a matrícula não existir, devolve {"erro": ...}.

    Args:
        matricula: matrícula do servidor, com 4 dígitos (ex.: "1002").
    """
    return servidor_publico(matricula)


@mcp.tool()
def consultar_escala(equipe: str, inicio: str, fim: str) -> dict:
    """Afastamentos já marcados na equipe que se sobrepõem ao período e o limite da regra N5
    (30% da equipe, arredondado para baixo, mínimo 1). Devolve tamanho_equipe, limite_afastados e
    afastamentos (matrícula, início e fim de cada um). Equipe inexistente -> {"erro": ...}.

    Args:
        equipe: nome da equipe (ex.: "Cartório"); acento e maiúscula não importam.
        inicio: primeiro dia do período, no formato AAAA-MM-DD.
        fim: último dia do período, no formato AAAA-MM-DD.
    """
    return escala_da_equipe(equipe, inicio, fim)


@mcp.tool()
def consultar_normas(tema: str) -> dict:
    """Regras da Norma Interna nº 1/2026 sobre um tema. Devolve {"tema", "regras": [{"codigo", "tema", "regra"}]}.

    Args:
        tema: um destes: "férias", "prazos", "escala", "abono", "diária", "aprovação", "saúde" ou "dados"
            (acento e maiúscula não importam).
    """
    # Dict e não lista: uma tool MCP que devolve LISTA manda cada item como um conteúdo separado.
    return {"tema": tema, "regras": normas(tema)}


@mcp.tool()
def tabela_diarias(tipo_destino: str) -> dict:
    """Moeda e valor de UMA diária para o tipo de destino (exterior é em USD). Tipo inválido -> {"erro": ...}.

    Args:
        tipo_destino: "capital", "interior" ou "exterior".
    """
    return diaria(tipo_destino)


@mcp.tool()
def consultar_remuneracao(matricula: str) -> dict:
    """[RESTRITA] Salário-base de um servidor. Use SOMENTE para calcular a venda de férias (N2: salario_base / 30
    por dia vendido). Matrícula inexistente -> {"erro": ...}.

    Args:
        matricula: matrícula do servidor, com 4 dígitos (ex.: "1002").
    """
    return remuneracao(matricula)


# ------------------------------------------------------------------ ETAPA 5.2 / 9.2 / 9.3: escrita
def _auditar(protocolo: str, decisao_acesso: str, motivo: str, parametros: dict) -> None:
    # ETAPA 9.3 -- acrescente UMA linha JSON em ARQUIVO_AUDITORIA com: quando (datetime.now().isoformat()),
    #   thread_id, quem pediu (aprovador), tool ("registrar_decisao"), protocolo, decisao_acesso ("ALLOW"/"DENY"),
    #   motivo e os parâmetros (SEM o token e SEM o despacho inteiro).
    #   Referência: aula5/exemplos/10_seguranca/server_seguro.py (_registrar_auditoria), aqui em JSON Lines.
    linha = {"quando": datetime.now().isoformat(), "thread_id": parametros.get("thread_id", ""),
             "quem": parametros.get("aprovador"), "tool": "registrar_decisao", "protocolo": protocolo,
             "decisao_acesso": decisao_acesso, "motivo": motivo,
             "parametros": parametros}  # `parametros` já vem SEM o token e SEM o despacho
    with ARQUIVO_AUDITORIA.open("a", encoding="utf-8") as arquivo:
        arquivo.write(json.dumps(linha, ensure_ascii=False) + "\n")


def _negar(protocolo: str, motivo: str, parametros: dict) -> dict:
    """Audita a negação (DENY) e devolve o erro para quem chamou."""
    _auditar(protocolo, "DENY", motivo, parametros)
    return {"sucesso": False, "erro": motivo}


@mcp.tool()
def registrar_decisao(protocolo: str, matricula: str, tipo: str, inicio: str, fim: str, decisao: str,
                      aprovador: str, despacho: str, token: str, thread_id: str = "") -> dict:
    """[ESCRITA -- requer autorização] Grava a decisão de um pedido. É IDEMPOTENTE: se o protocolo já foi
    gravado, não grava de novo e devolve {"sucesso": True, "duplicado": True}.

    Args:
        protocolo: protocolo idempotente do pedido (ferramentas.gerar_protocolo).
        matricula: matrícula do servidor que fez o pedido.
        tipo: "ferias", "abono" ou "diaria".
        inicio: primeiro dia do período (AAAA-MM-DD).
        fim: último dia do período (AAAA-MM-DD).
        decisao: "deferido", "indeferido" ou "indeferido_pela_chefia".
        aprovador: matrícula de quem aprovou (a chefia) ou "sistema" quando não precisa de chefia.
        despacho: texto do despacho (sem CPF nem dado de saúde, N10).
        token: token de registro. Sem ele, a gravação é negada.
        thread_id: identificador da execução do grafo (para a auditoria).
    """
    parametros = {"matricula": matricula, "tipo": tipo, "inicio": inicio, "fim": fim, "decisao": decisao,
                  "aprovador": aprovador, "thread_id": thread_id}

    # ETAPA 9.2 -- ANTES de gravar, negue (e audite com DENY) quando:
    #   - token != TOKEN_REGISTRO (ou TOKEN_REGISTRO vazio);
    #   - decisao não está em DECISOES_VALIDAS;
    #   - aprovador == matricula (ninguém aprova o próprio pedido, N8);
    #   - decisao == "deferido" e tipo in TIPOS_COM_CHEFIA e aprovador != chefia do servidor no cadastro.
    #   Devolva {"sucesso": False, "erro": "..."} -- quem nega é o SERVER, não o agente.
    #   Referência: aula5/exemplos/10_seguranca/server_seguro.py (alterar_status_ocorrencia)
    if not TOKEN_REGISTRO or token != TOKEN_REGISTRO:
        return _negar(protocolo, "token inválido", parametros)
    if decisao not in DECISOES_VALIDAS:
        return _negar(protocolo, f"decisão inválida: {decisao!r} (use {sorted(DECISOES_VALIDAS)})", parametros)
    if str(aprovador).strip() == str(matricula).strip():
        return _negar(protocolo, "N8: ninguém aprova o próprio pedido", parametros)
    if decisao == "deferido" and tipo in TIPOS_COM_CHEFIA:
        servidor = carregar_servidores().get(str(matricula).strip())
        if servidor is None:
            return _negar(protocolo, "matrícula não encontrada", parametros)
        if str(aprovador).strip() != str(servidor["chefia"]).strip():
            return _negar(protocolo, f"N8: {tipo} só é deferido pela chefia imediata ({servidor['chefia']}), "
                                     f"não por {aprovador}", parametros)

    # ETAPA 5.2 -- grave em ARQUIVO_REGISTROS (use _ler_registros/_gravar_registros).
    #   Protocolo já existe -> NÃO grave de novo; devolva {"sucesso": True, "duplicado": True, ...}.
    #   Senão grave {**parametros, "despacho": despacho, "registrado_em": ...} e devolva {"sucesso": True, ...}.
    #   Audite com ALLOW (9.3).
    registros = _ler_registros()
    if protocolo in registros:
        _auditar(protocolo, "ALLOW", "duplicado", parametros)
        return {"sucesso": True, "duplicado": True, "protocolo": protocolo, "registro": registros[protocolo]}
    registros[protocolo] = {**parametros, "despacho": despacho, "registrado_em": datetime.now().isoformat()}
    _gravar_registros(registros)
    _auditar(protocolo, "ALLOW", "gravado", parametros)
    return {"sucesso": True, "duplicado": False, "protocolo": protocolo}


if __name__ == "__main__":
    mcp.run(transport="stdio")
