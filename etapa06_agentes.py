"""
ETAPA 6 -- Os especialistas, cada um com UMA responsabilidade e SÓ as tools de que precisa.

ESQUELETO:
  ETAPA 6.1 -- criar_normas(), criar_escala(), criar_financeiro(), criar_redator()
  ETAPA 6.2 -- criar_coordenador(): usa Normas, Escala e Financeiro como TOOLS (as_tool)

As tools abaixo (PRONTAS) só repassam a chamada ao MCP Server do RH (cliente_mcp.chamar_mcp_async). Dar a cada agente
só as tools dele é o PRIVILÉGIO MÍNIMO (Etapa 9.1); o hook do 🆕 B (etapa10_governanca.py) confere isso em tempo de execução.

REFERÊNCIAS NAS AULAS
  aula4/ex03_agente_as_tool.py                         Coordenador que chama especialistas como tools    <- o mais parecido
  aula2/agente_handoff2.py                             as_tool com tool_name e tool_description
  aula4/ex02_handoff_roteamento.py                     handoff: compare (quem responde no fim?)
  aula7/exemplos/02_agentes_especializados/main.py     uma responsabilidade por agente
  aula7/agentes.py                                     ModelSettings(temperature=0) para respostas mais estáveis
  aula8/exemplos/06_excessive_agency/1_codigo_pronto/main.py   o problema de dar tools DEMAIS a um agente
"""
import json

from agents import Agent, ModelSettings, RunHooks, function_tool

from etapa05_cliente_mcp import chamar_mcp_async
from etapa04_ferramentas import cotacao_usd_brl

CONFIG = ModelSettings(temperature=0)


# ------------------------------------------------------------------ tools (PRONTAS)
@function_tool
async def consultar_normas(tema: str) -> str:
    """Regras da norma interna sobre um tema: férias, prazos, escala, abono, diária, aprovação, saúde ou dados."""
    return json.dumps(await chamar_mcp_async("consultar_normas", {"tema": tema}), ensure_ascii=False)


@function_tool
async def consultar_escala(equipe: str, inicio: str, fim: str) -> str:
    """Afastamentos da equipe que se sobrepõem ao período (AAAA-MM-DD) e o limite de afastados da regra N5."""
    return json.dumps(await chamar_mcp_async("consultar_escala", {"equipe": equipe, "inicio": inicio, "fim": fim}),
                      ensure_ascii=False)


@function_tool
async def tabela_diarias(tipo_destino: str) -> str:
    """Moeda e valor da diária para o tipo de destino: capital, interior ou exterior."""
    return json.dumps(await chamar_mcp_async("tabela_diarias", {"tipo_destino": tipo_destino}), ensure_ascii=False)


@function_tool
async def consultar_remuneracao(matricula: str) -> str:
    """[RESTRITA] Salário-base de um servidor. Só para calcular a venda de férias (N2)."""
    print(f"[tool] consultar_remuneracao({matricula}) EXECUTOU")  # o 🆕 B prova que, barrada, ela NÃO executa
    return json.dumps(await chamar_mcp_async("consultar_remuneracao", {"matricula": matricula}), ensure_ascii=False)


@function_tool
def cotacao_dolar() -> str:
    """Cotação atual do dólar em reais (USD->BRL). Devolve 'indisponível' se a API falhar: nunca invente o câmbio."""
    cotacao = cotacao_usd_brl()
    return f"{cotacao:.4f}" if cotacao else "indisponível"


# ------------------------------------------------------------------ ETAPA 6.1: especialistas
def criar_normas() -> Agent:
    # ETAPA 6.1 -- Agent(name="Normas", instructions=..., tools=[consultar_normas], model_settings=CONFIG)
    #   Responsabilidade: dizer QUAIS regras se aplicam ao pedido e explicá-las em linguagem simples.
    #   NÃO calcula valores, NÃO consulta a escala. Recebe também as violações objetivas (etapa06_regras.py) para explicá-las.
    return Agent(
        name="Normas",
        instructions=(
            "Você é o especialista em NORMAS do RH. Sua única responsabilidade: dizer QUAIS regras da Norma Interna "
            "(N1 a N10) se aplicam ao pedido recebido e explicá-las em linguagem simples, citando o código de cada "
            "uma (ex.: N3). Use a ferramenta consultar_normas com o tema certo (férias, prazos, escala, abono, "
            "diária, aprovação) para ler o texto da regra; nunca cite regra de memória. Você também recebe as "
            "VIOLAÇÕES OBJETIVAS já calculadas pelo sistema: explique-as, NÃO as recalcule. NÃO calcule valores e NÃO "
            "consulte a escala da equipe: isso é com outros especialistas. Responda em português, em poucas linhas."
        ),
        tools=[consultar_normas],
        model_settings=CONFIG,
    )


def criar_escala() -> Agent:
    # ETAPA 6.1 -- tools=[consultar_escala]. Confere a N5 e, se houver conflito, SUGERE outro período
    #   (com o mesmo número de dias, começando num dia permitido).
    return Agent(
        name="Escala",
        instructions=(
            "Você é o especialista em ESCALA do RH. Sua única responsabilidade: conferir a regra N5 (na mesma "
            "equipe, no máximo 30% dos servidores, mínimo 1, podem estar afastados no mesmo dia). Use a ferramenta "
            "consultar_escala com a equipe e as datas do período (AAAA-MM-DD). Os afastamentos do PRÓPRIO servidor "
            "não contam contra ele. Se o número de outros afastados for maior ou igual ao limite_afastados, há "
            "conflito: diga quem está afastado e SUGIRA outro período com o MESMO número de dias, começando "
            "depois do fim do afastamento que conflita e num dia permitido (segunda a quarta não colada em "
            "feriado: nunca começar numa quinta, sexta, nem na véspera ou antevéspera de feriado, regra N3). "
            "Escreva as datas sugeridas no formato AAAA-MM-DD. Sem conflito, diga apenas que a escala está livre. "
            "NÃO calcule valores e NÃO explique outras normas. Responda em português, em poucas linhas."
        ),
        tools=[consultar_escala],
        model_settings=CONFIG,
    )


def criar_financeiro() -> Agent:
    # ETAPA 6.1 -- tools=[tabela_diarias, consultar_remuneracao, cotacao_dolar].
    #   Venda de férias (N2): dias_vendidos x salario_base / 30. Diárias (N7): uma por pernoite + meia no retorno;
    #   no exterior, em USD convertido pela cotação. Mostre a conta. Câmbio indisponível -> "a calcular".
    #   É o ÚNICO agente que vê o salário.
    return Agent(
        name="Financeiro",
        instructions=(
            "Você é o especialista FINANCEIRO do RH e o ÚNICO que pode ver o salário. Sua responsabilidade: calcular "
            "valores, MOSTRANDO A CONTA. (1) Venda de férias (N2): dias_vendidos x salario_base / 30; obtenha o "
            "salario_base com a ferramenta consultar_remuneracao (só para isso). (2) Diária (N7): uma diária por "
            "pernoite mais MEIA diária no dia do retorno; o número de pernoites é a diferença em dias entre a data "
            "de início e a de fim (ex.: de 02/03 a 05/03 são 3 pernoites, ou seja, 3,5 diárias). Use a ferramenta "
            "tabela_diarias com o tipo de destino (capital, interior ou exterior). No exterior a diária é em USD: "
            "converta para reais com a ferramenta cotacao_dolar, e se ela devolver 'indisponível' escreva 'a calcular' "
            "para o valor em reais, NUNCA invente o câmbio. (3) Abono simples e pedidos sem venda de dias nem diária: "
            "responda 'não se aplica'. Mostre sempre a conta (ex.: 10 x 9500 / 30 = 3166.67). Responda em português, "
            "em poucas linhas."
        ),
        tools=[tabela_diarias, consultar_remuneracao, cotacao_dolar],
        model_settings=CONFIG,
    )


def criar_redator() -> Agent:
    # ETAPA 6.1 -- sem tools. Escreve o DESPACHO (deferimento ou indeferimento) usando SÓ os fatos recebidos:
    #   matrícula, período, decisão, regras citadas, valores e, no indeferimento, a sugestão de datas.
    #   Nunca escreve CPF nem dado de saúde (N10). Se receber um feedback (da validação ou da chefia), corrige.
    return Agent(
        name="Redator",
        instructions=(
            "Você é o REDATOR de despachos do RH. Escreva o despacho (deferimento ou indeferimento) usando SOMENTE os "
            "fatos que receber: matrícula, período, decisão, regras citadas, valores e, no indeferimento, a sugestão "
            "de datas. O despacho DEVE conter: (a) a matrícula do servidor; (b) a data de início do período no "
            "formato AAAA-MM-DD; (c) a decisão, escrevendo a palavra 'deferido' ou 'indeferido'. No indeferimento, "
            "cite o código da regra violada (ex.: N5) e a sugestão de datas, se houver. Seja formal e breve (até "
            "5 linhas). NUNCA escreva CPF, CID nem informação de saúde (N10) e não invente fatos nem valores. Se "
            "receber um FEEDBACK (da validação ou da chefia), corrija o despacho conforme ele."
        ),
        model_settings=CONFIG,
    )


# ------------------------------------------------------------------ ETAPA 6.2: coordenador
def criar_coordenador(hooks: RunHooks | None = None, tools_extras: list | None = None) -> Agent:
    # ETAPA 6.2 -- Agent(name="Coordenador", instructions=..., tools=[
    #       criar_normas().as_tool(tool_name="parecer_normas", tool_description=..., hooks=hooks,
    #                              failure_error_function=None),
    #       criar_escala().as_tool(tool_name="parecer_escala", ..., hooks=hooks, failure_error_function=None),
    #       criar_financeiro().as_tool(tool_name="parecer_financeiro", ..., hooks=hooks, failure_error_function=None),
    #       *(tools_extras or [])])
    #   O Coordenador decide QUAIS especialistas consultar (abono não precisa do Financeiro) e junta os pareceres.
    #   hooks=hooks nas as_tool faz o hook do 🆕 B enxergar também as tools DENTRO de cada especialista.
    #   failure_error_function=None: sem isso, um erro dentro do especialista (inclusive o bloqueio do 🆕 B) vira só
    #   um texto de erro para o Coordenador, que segue em frente. Com None, o erro interrompe a execução.
    #   tools_extras serve só para o teste do 🆕 B (dar de propósito consultar_remuneracao ao Coordenador).
    #   Referência: aula4/ex03_agente_as_tool.py
    return Agent(
        name="Coordenador",
        instructions=(
            "Você é o COORDENADOR da análise de um pedido de RH. Você NÃO analisa sozinho: consulta os especialistas "
            "(ferramentas parecer_normas, parecer_escala e parecer_financeiro) e junta os pareceres. Decida QUAIS "
            "consultar: parecer_normas para explicar as regras do pedido; parecer_escala para férias e abono "
            "(conflito de equipe, N5); parecer_financeiro SÓ se houver venda de dias (dias_vendidos maior que 0) "
            "ou diária. Um abono simples NÃO precisa do Financeiro. Ao chamar um especialista, envie no texto TUDO o "
            "que ele precisa: tipo do pedido, matrícula, equipe, datas (AAAA-MM-DD), dias vendidos, destino e as "
            "violações objetivas. Ao final, escreva um parecer consolidado com as seções NORMAS, ESCALA e FINANCEIRO "
            "(omita a que não consultou), repassando os valores e as datas sugeridas exatamente como vieram, sem "
            "inventar nada."
        ),
        tools=[
            criar_normas().as_tool(
                tool_name="parecer_normas",
                tool_description="Parecer do especialista em Normas: quais regras da norma se aplicam ao pedido e por quê.",
                hooks=hooks, failure_error_function=None),
            criar_escala().as_tool(
                tool_name="parecer_escala",
                tool_description="Parecer do especialista em Escala: confere o limite de afastados da equipe (N5) e sugere novas datas.",
                hooks=hooks, failure_error_function=None),
            criar_financeiro().as_tool(
                tool_name="parecer_financeiro",
                tool_description="Parecer do especialista Financeiro: valor da venda de férias ou das diárias, com a conta.",
                hooks=hooks, failure_error_function=None),
            *(tools_extras or []),
        ],
        model_settings=CONFIG,
    )