"""
ETAPAS 7, 8, 9 e 10 (e o opcional 🆕 C) -- O sistema como GRAFO (LangGraph), com persistência, humanos no fluxo e segurança.

Reaproveite TUDO o que você já fez: cada nó só chama funções e agentes das etapas anteriores e devolve O QUE MUDOU
no estado (mais o próprio nome em "caminho").

ESQUELETO: implemente as etapas na ordem (troque cada raise NotImplementedError pelo código da etapa).
  ETAPA 7.1 -- confira o Estado (o reducer de `violacoes`)
  ETAPA 7.2 -- nós e roteadores da ENTRADA: proteger_entrada, extrair, identificar_servidor, calcular_periodo
  ETAPA 7.3 -- PARALELISMO: normas, escala e financeiro (fan-out) e consolidar (fan-in)
  ETAPA 7.4 -- CICLO: redigir_despacho <-> validar, no máximo 3 tentativas
  ETAPA 7.5 -- registrar (MCP) e construir(): nós, arestas fixas e condicionais
  ETAPA 8.1 -- compilar com o checkpointer SQLite (já está em main(); confira)
  ETAPA 8.2 -- aprovacao_chefia com interrupt()
  ETAPA 8.3 -- rotear_chefia: aprovado / rejeitado (máx. 2) / indeferido_pela_chefia
  ETAPA 8.4 -- retomar() com Command(resume=...) (o listar_pendentes() já vem pronto: leia)
  ETAPA 9.4 -- guardrail de SAÍDA no registrar (despacho sem CPF/CID, N10)
  (ETAPA 9.2/9.3 ficam no etapa05_mcp_rh.py; ETAPA 10 em etapa10_observabilidade.py e etapa10_governanca.py)
  --- OPCIONAIS (bônus): o grafo funciona sem eles. Faça só se sobrar tempo, depois da Etapa 9.
  🆕 A     -- ligue USAR_VOTACAO = True (depois de fazer o 🆕 A em etapa02_extracao.py)
  🆕 C.1   -- proteger_entrada com DUAS faixas (ok / dúvida / bloqueado)
  🆕 C.2   -- triagem_humana com interrupt() e rotear_triagem; depois ligue USAR_TRIAGEM_HUMANA = True

REFERÊNCIAS NAS AULAS
  aula6/exemplos/05_multiplos_nos/main.py          nós devolvem só o que mudou
  aula6/exemplos/06_fluxo_condicional/main.py      add_conditional_edges com roteador                      (7.2)
  aula6/exemplos/07_dag/main.py                    ramifica e converge                                     (7.3)
  aula7/exemplos/08_paralelismo/main.py            fan-out / fan-in por LISTA e o conflito de escrita      (7.3)
  aula6/exemplos/08_fluxo_ciclico/main.py          ciclo com condição de parada + recursion_limit          (7.4)
  aula6/exemplos/11_grafo_real/main.py             nós de LLM, MCP, API de feriados e tool local, num DAG + ciclo
  aula6/desafio2/main.py                           LLM + MCP + API + ciclo, com caminho percorrido         <- o mais parecido (7)
  aula7/exemplos/05_equipe_multiagente/main.py     especialistas como NÓS (aqui não há mais Coordenador)
  aula8/exemplos/10_agentes_e_grafo/1_codigo_pronto/main.py   agente decide, grafo controla
  aula7/exemplos/04_persistencia/main.py           SqliteSaver, thread_id, get_state, retomar em outro processo  (8.1)
  aula7/exemplos/06_human_in_the_loop/main.py      interrupt() e Command(resume=...)                       (8.2/8.4, 🆕 C)
  aula7/exemplos/07_aprovacao_revisao/main.py      rejeição com feedback e limite de tentativas            <- o mais parecido (8.3)
  aula7/desafio2/                                  duas pessoas decidindo no mesmo fluxo                   (🆕 C)
  aula8/exemplos/05_guardrail_saida/1_codigo_pronto/main.py   conferir a saída antes de entregar         (9.4)
  aula6/exemplos/04_langgraph_basico/main.py       app.get_graph().draw_mermaid()

Rodar (a partir de aula10/):
    python etapa07_grafo.py C2_ferias_com_venda           # um caso (o nome do caso é o thread_id)
    python etapa07_grafo.py --todos                       # os 12 casos
    python etapa07_grafo.py --pendentes                   # quem está pausado e onde (pronto)
    python etapa07_grafo.py --retomar C2_ferias_com_venda sim 1010             # a chefia 1010 aprova
    python etapa07_grafo.py --retomar C2_ferias_com_venda "nao:mude as datas" 1010
    python etapa07_grafo.py --retomar C10_zona_cinzenta "sim:tratar como abono" rh   # 🆕 C (opcional): o atendente do RH decide
    python etapa07_grafo.py --sem-guardrail C7_autoaprovacao   # ETAPA 9.5: o server ainda nega?
    python etapa07_grafo.py --mermaid                     # o diagrama
"""
import asyncio
import operator
import os
import re
import sys
from typing import Annotated, TypedDict

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

import etapa06_agentes as agentes
from etapa05_cliente_mcp import chamar_mcp
from dados.casos import CASOS
from dados_rh import SAIDAS
from etapa02_extracao import extrair, extrair_com_votacao
from etapa04_ferramentas import gerar_protocolo, rodar_com_limites_sync
from etapa03_guardrails import LIMIAR, MENSAGENS, encontrar_dado_sensivel, nota_escopo
from etapa10_observabilidade import medido
from provedor import configurar
from etapa06_regras import precisa_chefia, resumo_periodo, violacoes_objetivas

MODELO = configurar()

MAX_TENTATIVAS = 3     # ciclo redigir <-> validar
MAX_REJEICOES = 2      # ciclo chefia -> redigir
TIMEOUT_LLM = float(os.getenv("TIMEOUT_LLM", "300"))  # segundos por agente: o Ollama local atende um pedido por vez
USAR_VOTACAO = False          # 🆕 A (opcional): True usa os 3 extratores com votação (etapa02_extracao.py)
USAR_TRIAGEM_HUMANA = False   # 🆕 C (opcional): True liga as duas faixas e o nó triagem_humana
LIMIAR_OK = 0.4        # 🆕 C: abaixo disto, segue direto
LIMIAR_BLOQUEIO = 0.8  # 🆕 C: a partir disto, bloqueia; entre os dois, um humano decide
GUARDRAIL_LIGADO = "--sem-guardrail" not in sys.argv  # ETAPA 9.5


# ------------------------------------------------------------------ ETAPA 7.1: o estado
class Estado(TypedDict, total=False):
    thread_id: str
    texto: str
    bloqueado: bool
    motivo_bloqueio: str
    nota_escopo: float
    triagem: dict                 # 🆕 C: {"segue": bool, "orientacao": str, "quem": str}
    pedido: dict                  # Pedido.model_dump(mode="json")
    divergencia: bool             # 🆕 A
    servidor: dict
    periodo: dict
    escala: dict
    parecer_normas: str
    parecer_escala: str
    parecer_financeiro: str
    violacoes: Annotated[list[str], operator.add]   # ETAPA 7.1: reducer; ramos paralelos podem escrever no mesmo passo
    decisao: str                  # "deferido" | "indeferido" | "indeferido_pela_chefia"
    despacho: str
    tentativas: int
    erro_validacao: str
    rejeicoes: int
    feedback_chefia: str
    aprovador: str
    protocolo: str
    resultado: str                # o desfecho, para a tabela da Etapa 11
    caminho: Annotated[list[str], operator.add]   # cada nó só ACRESCENTA o próprio nome


# ------------------------------------------------------------------ nós PRONTOS
def receber(estado: Estado) -> dict:
    return {"texto": estado["texto"].strip(), "tentativas": 0, "rejeicoes": 0, "violacoes": [],
            "erro_validacao": "", "feedback_chefia": "", "caminho": ["receber"]}


def recusar(estado: Estado) -> dict:
    orientacao = (estado.get("triagem") or {}).get("orientacao")
    return {"resultado": "recusado", "despacho": orientacao or estado.get("motivo_bloqueio", ""), "caminho": ["recusar"]}


def pedir_dados(estado: Estado) -> dict:
    faltam = "a matrícula e as datas exatas (AAAA-MM-DD)"
    if estado.get("divergencia"):
        faltam = "as datas e o tipo do pedido (a leitura do seu texto ficou ambígua)"
    return {"resultado": "pediu_dados", "despacho": f"Para seguir, informe {faltam}.", "caminho": ["pedir_dados"]}


# ------------------------------------------------------------------ ETAPA 7.2: entrada
def proteger_entrada(estado: Estado) -> dict:
    # ETAPA 7.2 -- se GUARDRAIL_LIGADO:
    #   encontrar_dado_sensivel(texto) -> {"bloqueado": True, "motivo_bloqueio": MENSAGENS["cpf" ou "cid"]}
    #   nota, motivo = nota_escopo(texto); nota >= LIMIAR -> bloqueado com MENSAGENS["fora_do_escopo"]
    #   Guarde "nota_escopo" no estado. Sempre devolva "caminho": ["proteger_entrada"].
    # 🆕 C.1 (opcional) -- com USAR_TRIAGEM_HUMANA, troque o LIMIAR único por DUAS faixas: < LIMIAR_OK segue;
    #   >= LIMIAR_BLOQUEIO bloqueia; no meio, bloqueado=False com a nota no estado: o roteador manda para triagem_humana.
    if GUARDRAIL_LIGADO:
        achado = encontrar_dado_sensivel(estado["texto"])  # regex primeiro: barato e sem LLM
        if achado:
            return {"bloqueado": True, "motivo_bloqueio": MENSAGENS[achado], "caminho": ["proteger_entrada"]}
        nota, _ = nota_escopo(estado["texto"])
        if nota >= LIMIAR:
            return {"bloqueado": True, "motivo_bloqueio": MENSAGENS["fora_do_escopo"], "nota_escopo": nota,
                    "caminho": ["proteger_entrada"]}
        return {"bloqueado": False, "nota_escopo": nota, "caminho": ["proteger_entrada"]}
    return {"bloqueado": False, "caminho": ["proteger_entrada"]}  # --sem-guardrail (Etapa 9.5)


def rotear_entrada(estado: Estado) -> str:
    # ETAPA 7.2 -- "bloqueado" ou "ok".
    #   🆕 C.1 (opcional) -- com USAR_TRIAGEM_HUMANA, também "duvida" (nota entre LIMIAR_OK e LIMIAR_BLOQUEIO)
    return "bloqueado" if estado.get("bloqueado") else "ok"


def triagem_humana(estado: Estado) -> dict:
    # 🆕 C.2 (opcional) -- resposta = interrupt({"pergunta": "Este texto deve seguir como pedido ao RH?",
    #                                  "texto": ..., "nota": ..., "quem_decide": "atendente do RH"})
    #   `resposta` é o que o retomar() entregar: {"segue": bool, "orientacao": str, "quem": str}.
    #   Se segue, ANEXE a orientação ao texto (ex.: "[RH: tratar como abono]") para o extrator usar.
    #   Lembre: na retomada este nó roda DE NOVO desde o início -- nada com efeito colateral antes do interrupt().
    #   Conceito: aula7/exemplos/06_human_in_the_loop/main.py (lá o humano aprova a SAÍDA; aqui decide a ENTRADA)
    raise NotImplementedError("🆕 C.2: triagem_humana")


def rotear_triagem(estado: Estado) -> str:
    # 🆕 C.2 -- "segue" ou "recusa"
    raise NotImplementedError("🆕 C.2: rotear_triagem")


def no_extrair(estado: Estado) -> dict:
    # ETAPA 7.2 -- pedido = extrair(texto); divergencia = False
    #   🆕 A (opcional) -- se USAR_VOTACAO: pedido, divergencia, votos = asyncio.run(extrair_com_votacao(texto))
    #   Devolva {"pedido": pedido.model_dump(mode="json"), "divergencia": ..., "caminho": ["extrair"]}
    divergencia = False
    if USAR_VOTACAO:  # 🆕 A (opcional)
        pedido, divergencia, _ = asyncio.run(extrair_com_votacao(estado["texto"]))
    else:
        pedido = extrair(estado["texto"])
    return {"pedido": pedido.model_dump(mode="json"), "divergencia": divergencia, "caminho": ["extrair"]}


def rotear_extracao(estado: Estado) -> str:
    # ETAPA 7.2 -- "incompleto" se divergencia, ou sem matrícula, ou sem data_inicio/data_fim; senão "ok".
    #   (pedido de licença de saúde também não segue: N9 -- mande para "incompleto" ou crie um nó próprio)
    pedido = estado["pedido"]
    # licença de saúde ou "outro" não tramitam aqui (N9): também vão para o ramo "incompleto"
    if (estado.get("divergencia") or pedido["tipo"] not in ("ferias", "abono", "diaria")
            or not (pedido.get("matricula") and pedido.get("data_inicio") and pedido.get("data_fim"))):
        return "incompleto"
    return "ok"


def identificar_servidor(estado: Estado) -> dict:
    # ETAPA 7.2 -- servidor = chamar_mcp("consultar_servidor", {"matricula": ...}) -> {"servidor": servidor, ...}
    servidor = chamar_mcp("consultar_servidor", {"matricula": estado["pedido"]["matricula"]})
    return {"servidor": servidor, "caminho": ["identificar_servidor"]}


def rotear_servidor(estado: Estado) -> str:
    # ETAPA 7.2 -- "nao_encontrado" se o MCP devolveu {"erro": ...}; senão "ok"
    return "nao_encontrado" if "erro" in estado["servidor"] else "ok"


def calcular_periodo(estado: Estado) -> dict:
    # ETAPA 7.2 -- periodo = resumo_periodo(inicio, fim)  (API de feriados, Etapa 4)
    #              escala  = chamar_mcp("consultar_escala", {"equipe": ..., "inicio": ..., "fim": ...})
    #   Devolva os dois (e "fonte_feriados" se quiser a métrica da Etapa 10).
    pedido = estado["pedido"]
    periodo = resumo_periodo(pedido["data_inicio"], pedido["data_fim"])
    escala = chamar_mcp("consultar_escala", {"equipe": estado["servidor"]["equipe"],
                                             "inicio": pedido["data_inicio"], "fim": pedido["data_fim"]})
    return {"periodo": periodo, "escala": escala, "caminho": ["calcular_periodo"]}


# ------------------------------------------------------------------ ETAPA 7.3: paralelismo
def _fatos(estado: Estado) -> str:
    """(PRONTO) O que os especialistas recebem: pedido, servidor (sem salário), período e escala."""
    return (f"PEDIDO: {estado['pedido']}\nSERVIDOR: {estado['servidor']}\n"
            f"PERÍODO: {estado['periodo']}\nESCALA DA EQUIPE: {estado['escala']}")


def no_normas(estado: Estado) -> dict:
    # ETAPA 7.3 -- rode agentes.criar_normas() com rodar_com_limites_sync(_fatos(estado)) e escreva SÓ
    #   {"parecer_normas": ..., "caminho": ["normas"]}. Os três especialistas rodam no MESMO passo.
    #   Referência: aula7/exemplos/08_paralelismo/main.py (cada ramo escreve no SEU campo)
    resultado = rodar_com_limites_sync(agentes.criar_normas(), _fatos(estado), timeout_s=TIMEOUT_LLM)
    return {"parecer_normas": resultado.final_output, "caminho": ["normas"]}


def no_escala(estado: Estado) -> dict:
    # ETAPA 7.3 -- idem, agentes.criar_escala() -> {"parecer_escala": ..., "caminho": ["escala"]}
    resultado = rodar_com_limites_sync(agentes.criar_escala(), _fatos(estado), timeout_s=TIMEOUT_LLM)
    return {"parecer_escala": resultado.final_output, "caminho": ["escala"]}


def no_financeiro(estado: Estado) -> dict:
    # ETAPA 7.3 -- idem, agentes.criar_financeiro() -> {"parecer_financeiro": ..., "caminho": ["financeiro"]}
    #   (abono não tem valor: devolva "não se aplica" sem chamar o LLM -- economiza tokens)
    pedido = estado["pedido"]
    if pedido["tipo"] != "diaria" and not pedido.get("dias_vendidos"):  # sem venda de dias nem diária: sem valor
        return {"parecer_financeiro": "não se aplica", "caminho": ["financeiro"]}
    resultado = rodar_com_limites_sync(agentes.criar_financeiro(), _fatos(estado), timeout_s=TIMEOUT_LLM)
    return {"parecer_financeiro": resultado.final_output, "caminho": ["financeiro"]}


def consolidar(estado: Estado) -> dict:
    # ETAPA 7.3 -- o fan-in: espera os três ramos. Calcule violacoes_objetivas(...) (etapa06_regras.py) e a decisão:
    #   {"violacoes": [...], "decisao": "indeferido" se houver violação, senão "deferido", "caminho": ["consolidar"]}
    #   Experimento (Etapa 7.1): faça no_escala também devolver {"violacoes": ["teste"]}. Sem o reducer em
    #   `violacoes`, o que acontece? E com ele? (aula7/exemplos/08_paralelismo/exercicio.md, item 4)
    violacoes = violacoes_objetivas(estado["pedido"], estado["servidor"], estado["periodo"], estado["escala"])
    return {"violacoes": violacoes, "decisao": "indeferido" if violacoes else "deferido",
            "caminho": ["consolidar"]}


# ------------------------------------------------------------------ ETAPA 7.4: ciclo
def redigir_despacho(estado: Estado) -> dict:
    # ETAPA 7.4 -- monte a entrada do Redator com _fatos(estado), a decisão, as violações, os três pareceres
    #   e, se houver, estado["erro_validacao"] (da validação) e estado["feedback_chefia"] (da Etapa 8).
    #   Devolva {"despacho": ..., "caminho": ["redigir_despacho"]}.
    pedido = estado["pedido"]
    entrada = (f"DECISÃO: {estado['decisao'].upper()}\nMATRÍCULA: {pedido['matricula']}\n"
               f"INÍCIO DO PERÍODO: {pedido['data_inicio']}\n"
               f"VIOLAÇÕES: {estado['violacoes'] if estado['violacoes'] else 'nenhuma'}\n"
               f"PARECER DE NORMAS: {estado['parecer_normas']}\nPARECER DE ESCALA: {estado['parecer_escala']}\n"
               f"PARECER FINANCEIRO: {estado['parecer_financeiro']}\n\n{_fatos(estado)}")
    if estado.get("erro_validacao"):  # o ciclo: o validador reprovou o despacho anterior
        entrada += f"\n\nFEEDBACK DA VALIDAÇÃO (corrija o despacho): {estado['erro_validacao']}"
    if estado.get("feedback_chefia"):  # Etapa 8: a chefia rejeitou
        entrada += f"\n\nFEEDBACK DA CHEFIA (corrija o despacho): {estado['feedback_chefia']}"
    resultado = rodar_com_limites_sync(agentes.criar_redator(), entrada, timeout_s=TIMEOUT_LLM)
    return {"despacho": resultado.final_output, "caminho": ["redigir_despacho"]}


def problemas_do_despacho(despacho: str, pedido: dict) -> list[str]:
    """(PRONTO) O que falta ou sobra no despacho: precisa citar a matrícula, o início do período (AAAA-MM-DD ou
    DD/MM/AAAA) e a decisão, e não pode ter CPF nem CID (N10). Lista vazia = despacho válido."""
    problemas, texto = [], despacho.lower()
    if pedido.get("matricula") and pedido["matricula"] not in despacho:
        problemas.append(f"cite a matrícula {pedido['matricula']}")
    inicio = pedido.get("data_inicio") or ""
    if inicio and inicio not in despacho and "/".join(reversed(inicio.split("-"))) not in despacho:
        problemas.append(f"cite o início do período ({inicio})")
    if "deferid" not in texto and "indeferid" not in texto:
        problemas.append("diga a decisão (deferido ou indeferido)")
    if encontrar_dado_sensivel(despacho):
        problemas.append("tire o CPF/CID do despacho (N10)")
    return problemas


def validar(estado: Estado) -> dict:
    # ETAPA 7.4 -- use problemas_do_despacho(estado["despacho"], estado["pedido"]) (pronta, logo acima) e devolva
    #   {"tentativas": estado["tentativas"] + 1, "erro_validacao": "; ".join(problemas), "caminho": ["validar"]}
    #   O ponto da etapa é o CICLO: é esta contagem de tentativas que o rotear_validacao usa para parar.
    problemas = problemas_do_despacho(estado["despacho"], estado["pedido"])
    return {"tentativas": estado["tentativas"] + 1, "erro_validacao": "; ".join(problemas), "caminho": ["validar"]}


def rotear_validacao(estado: Estado) -> str:
    # ETAPA 7.4 -- erro e tentativas < MAX_TENTATIVAS -> "redigir"
    #              (válido OU estourou as tentativas)   -> "chefia" se precisa_chefia(pedido, violacoes)
    #                                                      senão "registrar"
    #   (antes da Etapa 8, mande "chefia" também para "registrar": ainda não há o nó da chefia)
    if estado["erro_validacao"] and estado["tentativas"] < MAX_TENTATIVAS:
        return "redigir"  # o ciclo: reprovado e ainda há tentativas
    return "chefia" if precisa_chefia(estado["pedido"], estado["violacoes"]) else "registrar"


# ------------------------------------------------------------------ ETAPA 8: a chefia
def aprovacao_chefia(estado: Estado) -> dict:
    # ETAPA 8.2 -- resposta = interrupt({"pergunta": "Aprova o despacho?", "chefia": estado["servidor"]["chefia"],
    #                                    "resumo": <servidor, período, pareceres, despacho>})
    #   `resposta` = {"aprovado": bool, "aprovador": str, "motivo": str} (o que o retomar() entregar).
    #   Aprovado  -> {"aprovador": ..., "feedback_chefia": ""}
    #   Rejeitado -> {"rejeicoes": +1, "feedback_chefia": motivo, "aprovador": ...}
    #   NADA de gravar antes do interrupt(): na retomada, o nó roda de novo desde o começo.
    #   Referência: aula7/exemplos/07_aprovacao_revisao/main.py
    # Na retomada este nó roda DE NOVO desde a primeira linha: por isso NADA com efeito colateral antes do interrupt().
    resposta = interrupt({
        "pergunta": "Aprova o despacho?",
        "chefia": estado["servidor"]["chefia"],
        "resumo": {
            "servidor": estado["servidor"], "periodo": estado["periodo"],
            "pareceres": {"normas": estado["parecer_normas"], "escala": estado["parecer_escala"],
                          "financeiro": estado["parecer_financeiro"]},
            "despacho": estado["despacho"],
        },
    })  # o grafo PARA aqui; o valor de Command(resume=...) volta como `resposta`
    if resposta["aprovado"]:
        return {"aprovador": resposta["aprovador"], "feedback_chefia": "", "caminho": ["aprovacao_chefia"]}
    # Rejeitou: conta a rejeição e guarda o motivo (nunca vazio: é ele que diz ao roteador que foi rejeição)
    return {"rejeicoes": estado["rejeicoes"] + 1,
            "feedback_chefia": resposta.get("motivo") or "Rejeitado pela chefia, sem motivo informado.",
            "aprovador": resposta["aprovador"], "caminho": ["aprovacao_chefia"]}


def rotear_chefia(estado: Estado) -> str:
    # ETAPA 8.3 -- aprovado -> "registrar"; rejeitado e rejeicoes < MAX_REJEICOES -> "redigir";
    #              rejeitado de novo -> "registrar" (com decisao "indeferido_pela_chefia": ajuste no registrar)
    if not estado["feedback_chefia"]:  # aprovou (a aprovação zera o feedback)
        return "registrar"
    if estado["rejeicoes"] < MAX_REJEICOES:
        return "redigir"  # rejeitou e ainda há tentativas: o Redator refaz com o motivo da chefia
    return "registrar"  # rejeitou de novo: registra como "indeferido_pela_chefia" (ajuste feito no registrar)


# ------------------------------------------------------------------ ETAPA 7.5 / 9.4: registrar
def registrar(estado: Estado) -> dict:
    # ETAPA 7.5 -- protocolo = gerar_protocolo(...); chamar_mcp("registrar_decisao", {..., "token":
    #   os.getenv("TOKEN_REGISTRO", ""), "thread_id": estado["thread_id"], "aprovador": estado.get("aprovador")
    #   ou "sistema"}). Se rejeicoes >= MAX_REJEICOES, decisao = "indeferido_pela_chefia".
    #   Devolva {"protocolo": ..., "resultado": "registrado" ou "negado_pelo_server", "caminho": ["registrar"]}.
    # ETAPA 9.4 -- ANTES de gravar: encontrar_dado_sensivel(despacho) -> não grava, resultado "bloqueado_na_saida".
    pedido = estado["pedido"]
    protocolo = gerar_protocolo(pedido["matricula"], pedido["tipo"], pedido["data_inicio"], pedido["data_fim"])
    decisao = "indeferido_pela_chefia" if estado.get("rejeicoes", 0) >= MAX_REJEICOES else estado["decisao"]
    if encontrar_dado_sensivel(estado["despacho"]):  # ETAPA 9.4: guardrail de SAÍDA (N10): CPF/CID nunca é gravado
        return {"protocolo": protocolo, "resultado": "bloqueado_na_saida", "decisao": decisao, "caminho": ["registrar"]}
    registro = chamar_mcp("registrar_decisao", {
        "protocolo": protocolo, "matricula": pedido["matricula"], "tipo": pedido["tipo"],
        "inicio": pedido["data_inicio"], "fim": pedido["data_fim"], "decisao": decisao,
        "aprovador": estado.get("aprovador") or "sistema", "despacho": estado["despacho"],
        "token": os.getenv("TOKEN_REGISTRO", ""), "thread_id": estado["thread_id"]})
    if not registro.get("sucesso"):
        resultado = f"negado_pelo_server: {registro.get('erro')}"
    else:
        resultado = "duplicado" if registro.get("duplicado") else "registrado"
    return {"protocolo": protocolo, "resultado": resultado, "decisao": decisao, "caminho": ["registrar"]}


# ------------------------------------------------------------------ ETAPA 7.5: o grafo
def construir(checkpointer=None):
    construtor = StateGraph(Estado)
    nos = {
        "receber": receber, "proteger_entrada": proteger_entrada,
        "recusar": recusar, "extrair": no_extrair, "pedir_dados": pedir_dados,
        "identificar_servidor": identificar_servidor, "calcular_periodo": calcular_periodo,
        "normas": no_normas, "escala": no_escala, "financeiro": no_financeiro, "consolidar": consolidar,
        "redigir_despacho": redigir_despacho, "validar": validar, "aprovacao_chefia": aprovacao_chefia,
        "registrar": registrar,
    }
    if USAR_TRIAGEM_HUMANA:  # 🆕 C (opcional): o nó só entra no grafo quando o item está ligado
        nos["triagem_humana"] = triagem_humana
    for nome, funcao in nos.items():
        construtor.add_node(nome, medido(nome, funcao))  # medido(): ETAPA 10.2 (por enquanto, não muda nada)

    # ETAPA 7.5 -- ligue o grafo do ENUNCIADO.md:
    #   add_edge(START, "receber"); add_edge("receber", "proteger_entrada")
    #   add_conditional_edges("proteger_entrada", rotear_entrada, {"bloqueado": "recusar", "ok": "extrair"})
    #   🆕 C (opcional) -- se USAR_TRIAGEM_HUMANA: acrescente "duvida": "triagem_humana" ao dicionário acima e
    #                      add_conditional_edges("triagem_humana", rotear_triagem, {"segue": "extrair", "recusa": "recusar"})
    #   ... extrair, identificar_servidor (condicionais) -> calcular_periodo
    #   fan-out: calcular_periodo -> normas, escala, financeiro
    #   fan-in : add_edge(["normas", "escala", "financeiro"], "consolidar")   (LISTA: espera os três)
    #   consolidar -> redigir_despacho -> validar -> rotear_validacao {"redigir", "chefia", "registrar"}
    #   aprovacao_chefia -> rotear_chefia {"redigir", "registrar"}
    #   recusar, pedir_dados e registrar -> END
    construtor.add_edge(START, "receber")
    construtor.add_edge("receber", "proteger_entrada")

    destinos_entrada = {"bloqueado": "recusar", "ok": "extrair"}
    if USAR_TRIAGEM_HUMANA:  # 🆕 C (opcional)
        destinos_entrada["duvida"] = "triagem_humana"
        construtor.add_conditional_edges("triagem_humana", rotear_triagem, {"segue": "extrair", "recusa": "recusar"})
    construtor.add_conditional_edges("proteger_entrada", rotear_entrada, destinos_entrada)

    construtor.add_conditional_edges("extrair", rotear_extracao, {"incompleto": "pedir_dados", "ok": "identificar_servidor"})
    construtor.add_conditional_edges("identificar_servidor", rotear_servidor,
                                     {"nao_encontrado": "pedir_dados", "ok": "calcular_periodo"})

    for especialista in ("normas", "escala", "financeiro"):  # fan-out: os três rodam no MESMO passo
        construtor.add_edge("calcular_periodo", especialista)
    construtor.add_edge(["normas", "escala", "financeiro"], "consolidar")  # fan-in: LISTA = espera os três

    construtor.add_edge("consolidar", "redigir_despacho")
    construtor.add_edge("redigir_despacho", "validar")
    construtor.add_conditional_edges("validar", rotear_validacao,
                                     {"redigir": "redigir_despacho", "chefia": "aprovacao_chefia", "registrar": "registrar"})
    # ETAPA 8.3: a chefia aprova (registra) ou rejeita (o Redator refaz, no máximo MAX_REJEICOES vezes)
    construtor.add_conditional_edges("aprovacao_chefia", rotear_chefia,
                                     {"redigir": "redigir_despacho", "registrar": "registrar"})

    for fim in ("recusar", "pedir_dados", "registrar"):
        construtor.add_edge(fim, END)
    return construtor.compile(checkpointer=checkpointer)


# ------------------------------------------------------------------ execução
def config_de(thread_id: str) -> dict:
    """(PRONTO) thread_id identifica a execução no checkpointer; recursion_limit protege contra ciclo infinito."""
    return {"configurable": {"thread_id": thread_id}, "recursion_limit": 40}


def mostrar(app, thread_id: str, saida: dict) -> None:
    """(PRONTO) Imprime o caminho, o desfecho e, se pausou, a pergunta feita ao humano."""
    print(f"\n[{thread_id}] caminho: {' -> '.join(saida.get('caminho', []))}")
    if "__interrupt__" in saida:
        pausa = saida["__interrupt__"][0].value
        print(f"[{thread_id}] PAUSADO em {app.get_state(config_de(thread_id)).next}: {pausa.get('pergunta')}")
        print(f"   retome com: python etapa07_grafo.py --retomar {thread_id} sim|\"nao:motivo\" <quem>")
    else:
        print(f"[{thread_id}] resultado: {saida.get('resultado')}  protocolo: {saida.get('protocolo')}")
        print(f"[{thread_id}] despacho: {saida.get('despacho')}")


def executar(app, thread_id: str, texto: str) -> None:
    """(PRONTO) Roda um pedido do começo. Apaga antes os checkpoints desse thread_id: sem isso, rodar o mesmo caso
    de novo CONTINUARIA a execução anterior, e os campos com reducer (caminho, violacoes) acumulariam as duas."""
    if app.checkpointer:
        app.checkpointer.delete_thread(thread_id)
    saida = app.invoke({"thread_id": thread_id, "texto": texto}, config_de(thread_id))
    mostrar(app, thread_id, saida)


def listar_pendentes(app, checkpointer) -> None:
    """(PRONTO) Cada execução (thread_id) que está no checkpointer e onde ela parou. Quem tem `next` não vazio está
    PAUSADO esperando um humano. É o get_state() da aula7/exemplos/04_persistencia/main.py, para todas as execuções."""
    vistos = []
    for checkpoint in checkpointer.list(None):  # todos os checkpoints de todas as execuções
        thread_id = checkpoint.config["configurable"]["thread_id"]
        if thread_id not in vistos:
            vistos.append(thread_id)
    for thread_id in vistos:
        proximo = app.get_state(config_de(thread_id)).next
        print(f"{thread_id}: {'PAUSADO em ' + str(proximo) if proximo else 'concluído'}")


def retomar(app, thread_id: str, resposta: str, quem: str) -> None:
    # ETAPA 8.4 -- veja ONDE a execução parou: app.get_state(config_de(thread_id)).next
    #   - ("aprovacao_chefia",): valor = {"aprovado": resposta == "sim", "aprovador": quem,
    #                                    "motivo": o texto depois de "nao:"}
    #   - ("triagem_humana",)  : valor = {"segue": resposta começa com "sim", "orientacao": o texto depois de ":",
    #                                    "quem": quem}                                    (só com o 🆕 C, opcional)
    #   saida = app.invoke(Command(resume=valor), config_de(thread_id)); mostrar(app, thread_id, saida)
    #   Nada pausado? Diga isso e não faça nada (e se retomarem duas vezes?).
    #   Referência: aula7/exemplos/06_human_in_the_loop/main.py (retomar)
    proximo = app.get_state(config_de(thread_id)).next
    if not proximo:  # nada pausado (já terminou, nunca rodou ou já foi retomado): não faz nada
        print(f"[{thread_id}] nada pausado")
        return
    sim = resposta.strip().lower().startswith("sim")
    texto_depois = resposta.split(":", 1)[1].strip() if ":" in resposta else ""
    if proximo == ("aprovacao_chefia",):
        valor = {"aprovado": sim, "aprovador": quem, "motivo": texto_depois}
    elif proximo == ("triagem_humana",):  # 🆕 C (opcional)
        valor = {"segue": sim, "orientacao": texto_depois, "quem": quem}
    else:
        print(f"[{thread_id}] pausado em {proximo}: não sei retomar aqui")
        return
    saida = app.invoke(Command(resume=valor), config_de(thread_id))
    mostrar(app, thread_id, saida)


def main() -> None:
    args = [a for a in sys.argv[1:] if a != "--sem-guardrail"]
    # ETAPA 8.1 -- o checkpointer SQLite: é ele que deixa a execução pausada sobreviver ao fim do processo.
    with SqliteSaver.from_conn_string(str(SAIDAS / "checkpoints.db")) as checkpointer:
        app = construir(checkpointer)
        if not args or args[0] == "--mermaid":
            print(app.get_graph().draw_mermaid())
        elif args[0] == "--pendentes":
            listar_pendentes(app, checkpointer)
        elif args[0] == "--retomar":
            retomar(app, args[1], args[2], args[3] if len(args) > 3 else "?")
        elif args[0] == "--todos":
            for nome, caso in CASOS.items():
                executar(app, nome, caso["pedido"])
        else:
            executar(app, args[0], CASOS[args[0]]["pedido"])


if __name__ == "__main__":
    print(f"[modelo] {MODELO}  [guardrail de entrada {'ligado' if GUARDRAIL_LIGADO else 'DESLIGADO'}]")
    main()