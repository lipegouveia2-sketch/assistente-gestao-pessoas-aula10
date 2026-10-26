"""
ETAPA 2 -- Extração estruturada do pedido (15 min) e 🆕 A -- votação em paralelo (OPCIONAL, bônus).

ESQUELETO: implemente as etapas na ordem (troque cada raise NotImplementedError pelo código da etapa).
  ETAPA 2.1 -- complete o modelo Pydantic `Pedido` (campos + Field(description=...))
  ETAPA 2.2 -- criar_extrator(): um Agent com output_type=Pedido
  ETAPA 2.3 -- extrair(): roda UM extrator e devolve um Pedido
  --- daqui para baixo é OPCIONAL (item 🆕 A, vale bônus). Pule se estiver sem tempo: o resto do sistema não depende dele.
  🆕 A.1    -- INSTRUCOES_VOTANTES: 3 instruções ligeiramente diferentes
  🆕 A.2    -- extrair_com_votacao(): os 3 extratores AO MESMO TEMPO (asyncio.gather)
  (PRONTO)  -- votar(): maioria por campo; sem maioria em tipo/datas -> divergencia=True
  (PRONTO)  -- no __main__: o tempo em paralelo x em sequência

REFERÊNCIAS NAS AULAS
  aula2/agente_output.py                   output_type com um BaseModel simples
  aula2/agente_output2.py                  Enum + listas + Field(description=...)          <- o mais parecido
  aula7/exemplos/08_paralelismo/main.py    🆕 A: o CONCEITO de paralelismo (lá: tarefas DIFERENTES para ganhar tempo;
                                           aqui: a MESMA tarefa 3 vezes para ganhar confiabilidade)
  aula3/agente_parada3.py                  Runner.run (assíncrono) dentro de asyncio, como em A.2
  aula3/agente_retry.py                    por que a data de hoje entra nas instruções (já feito abaixo, com texto_hoje())

Rodar:
    python etapa02_extracao.py            # extrai C1, C2, C5 e C11
    python etapa02_extracao.py --votacao  # 🆕 A: C1, C2 e C9 com 3 votos e o tempo medido

Pronto quando: C2 sai com dias_vendidos=10, C11 com tipo_destino="exterior", C5 com matricula=None;
               na votação, C1/C2 unânimes e C9 mostrando os votos.
"""
import asyncio
import re
import sys
import time
from collections import Counter
from datetime import date, timedelta
from enum import Enum

from agents import Agent, ModelSettings, Runner
from pydantic import BaseModel, Field

from dados.casos import CASOS
from etapa04_ferramentas import texto_hoje, tokens_usados
from provedor import configurar

MODELO = configurar()


class TipoPedido(str, Enum):
    FERIAS = "ferias"
    ABONO = "abono"
    DIARIA = "diaria"
    LICENCA_SAUDE = "licenca_saude"
    OUTRO = "outro"


class TipoDestino(str, Enum):
    CAPITAL = "capital"
    INTERIOR = "interior"
    EXTERIOR = "exterior"


class Pedido(BaseModel):
    # ETAPA 2.1 -- complete os campos. O primeiro já está pronto como modelo.
    #   matricula: str | None          (4 dígitos; None se o servidor não informou)
    #   data_inicio: str | None        (AAAA-MM-DD)
    #   data_fim: str | None           (AAAA-MM-DD; no abono, igual à data_inicio)
    #   dias_vendidos: int             (0 a 10; abono pecuniário, regra N2 -- use Field(default=0, ge=0, le=10, ...))
    #   destino: str | None            (só para diária: a cidade)
    #   tipo_destino: TipoDestino | None
    #   justificativa: str             (resumo do motivo, sem dados de saúde)
    #   Escreva a DESCRIPTION de cada um: é a "instrução" que o LLM lê para preencher o campo.
    #   Referência: aula2/agente_output2.py (Ocorrencia, Envolvido, PapelEnvolvido)
    tipo: TipoPedido = Field(description=(
        "Tipo do pedido: 'ferias' (descanso anual, pode incluir venda de dias), 'abono' (folga de 1 dia útil), "
        "'diaria' (viagem a serviço), 'licenca_saude' (afastamento por doença) ou 'outro'."))
    matricula: str | None = Field(default=None, description=(
        "Matrícula do servidor: exatamente 4 dígitos (ex.: '1002'). Use null se o servidor NÃO informou a matrícula; "
        "nunca invente nem deduza a matrícula."))
    data_inicio: str | None = Field(default=None, description=(
        "Data de início no formato AAAA-MM-DD (ex.: '12/04/2027' vira '2027-04-12'). Use null se o servidor não disse "
        "o DIA exato: 'em agosto', 'perto da Páscoa' ou 'semana que vem' NÃO são datas exatas e devem ficar null. "
        "Nunca invente uma data."))
    data_fim: str | None = Field(default=None, description=(
        "Data de término no formato AAAA-MM-DD. No abono (folga de 1 dia), é igual à data_inicio. "
        "Use null se o servidor não disse o dia exato do fim; nunca invente uma data."))
    dias_vendidos: int = Field(default=0, ge=0, le=10, description=(
        "Quantidade de dias de férias que o servidor quer VENDER (abono pecuniário), de 0 a 10. "
        "Só preencha se ele pediu explicitamente um número de dias para vender (ex.: 'vender 10 dias'); "
        "'talvez vender uma parte' ou nenhuma menção = 0."))
    destino: str | None = Field(default=None, description=(
        "Cidade de destino da viagem. Só para diária (ex.: 'Buenos Aires'); null nos outros tipos de pedido."))
    tipo_destino: TipoDestino | None = Field(default=None, description=(
        "Só para diária: 'exterior' se a cidade fica fora do Brasil, 'capital' se é capital de estado brasileiro, "
        "'interior' para as demais cidades do Brasil. null nos outros tipos de pedido."))
    justificativa: str = Field(default="", description=(
        "Resumo curto do motivo do pedido, em uma frase. NUNCA inclua dados de saúde, CID, laudo ou CPF."))


def criar_extrator(instrucoes: str, input_guardrails: list | None = None) -> Agent:
    # ETAPA 2.2 -- devolva Agent(name="Extrator", instructions=instrucoes, output_type=Pedido,
    #   input_guardrails=input_guardrails or [])
    #   (os guardrails só entram na Etapa 3; até lá, a lista fica vazia)
    return Agent(
        name="Extrator",
        instructions=instrucoes,
        output_type=Pedido,
        input_guardrails=input_guardrails or [],
        model_settings=ModelSettings(temperature=0),  # extração não é criativa: menos variação entre execuções
    )


INSTRUCOES_EXTRATOR = (
    f"Hoje é {texto_hoje()}. "  # (PRONTO) a data real: o modelo não tem relógio (aula3/agente_retry.py)
    "Você extrai pedidos de servidores para o RH (férias, abono ou diária) e preenche os campos do esquema. "
    "Regras: (1) datas SEMPRE no formato AAAA-MM-DD; use a data de hoje acima só para entender 'amanhã' ou "
    "'semana que vem' quando o texto der um dia exato. "
    "(2) NÃO invente nada que o servidor não disse: matrícula ausente = null; mês sem dia exato "
    "(ex.: 'em agosto', 'perto da Páscoa') = data null, nunca escolha um dia. "
    "(3) No abono (folga de 1 dia), data_fim é igual à data_inicio. "
    "(4) dias_vendidos só se o servidor pedir explicitamente um número de dias para vender; senão 0. "
    "(5) Em diária, preencha destino (a cidade) e tipo_destino: exterior (fora do Brasil), capital ou interior. "
    "(6) Na justificativa, resuma o motivo em uma frase, sem CPF nem dados de saúde."
)


def extrair(texto: str) -> Pedido:
    # ETAPA 2.3 -- rode criar_extrator(INSTRUCOES_EXTRATOR) com Runner.run_sync e devolva final_output
    #   (é um objeto Pedido, não texto!)
    pedido, _ = extrair_com_uso(texto)
    return pedido


def extrair_com_uso(texto: str) -> tuple[Pedido, int]:
    """Extrai o pedido e também devolve o uso de tokens da chamada ao modelo."""
    resultado = Runner.run_sync(criar_extrator(INSTRUCOES_EXTRATOR), texto)
    pedido = conferir_com_texto(resultado.final_output, texto)
    return pedido, tokens_usados(resultado)


# Modelos pequenos (llama3.1) erram à toa a matrícula e as datas: ora devolvem null, ora "I0004", ora inventam
# "2027-08-01" para "em agosto". Esses campos têm formato fixo, então uma função Python confere o que o LLM extraiu
# contra o próprio texto -- mesma ideia das regras objetivas: um LLM pode errar, uma regex não.
RE_MATRICULA = re.compile(r"matr[ií]cula\D{0,10}?(\d{4})\b", re.IGNORECASE)
RE_DATA = re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b")
RE_VENDER = re.compile(r"vender\s+(\d{1,2})\s+dias", re.IGNORECASE)
RE_N_DIAS = re.compile(r"\b(\d{1,3})\s+dias\b", re.IGNORECASE)


def _data_iso(valor: str | None) -> str | None:
    """A data se estiver em AAAA-MM-DD válido; senão None."""
    try:
        return date.fromisoformat(valor).isoformat() if valor else None
    except ValueError:
        return None


def conferir_com_texto(pedido: Pedido, texto: str, hoje: date | None = None) -> Pedido:
    """Corrige matrícula, datas e dias vendidos com o que está ESCRITO no texto (o resto fica como o LLM extraiu).

    - matrícula: só vale a que aparece depois de "matrícula"; sem ela, None (nunca inventada);
    - datas: as DD/MM/AAAA do texto, na ordem (uma só = início; no abono, fim = início). Sem data escrita, só aceita
      "hoje"/"amanhã" (dia exato) e calcula o fim por "N dias"; "em agosto" ou "perto da Páscoa" ficam None;
    - dias_vendidos: só com "vender N dias"; "talvez vender uma parte" = 0.
    """
    hoje = hoje or date.today()
    achou = RE_MATRICULA.search(texto)
    matricula = achou.group(1) if achou else None

    datas = []
    for d, m, a in RE_DATA.findall(texto):
        try:
            datas.append(date(int(a), int(m), int(d)).isoformat())
        except ValueError:
            pass
    minusculo = texto.lower()
    if datas:
        inicio = datas[0]
        fim = datas[1] if len(datas) > 1 else None
    elif "depois de amanhã" in minusculo:
        inicio, fim = (hoje + timedelta(days=2)).isoformat(), None
    elif "amanhã" in minusculo or "amanha" in minusculo:
        inicio, fim = (hoje + timedelta(days=1)).isoformat(), None
    elif re.search(r"\bhoje\b", minusculo):
        inicio, fim = hoje.isoformat(), None
    else:
        inicio, fim = None, None  # sem dia exato no texto: não aceita data "chutada" pelo LLM

    if inicio and not fim:
        n_dias = RE_N_DIAS.search(texto)
        if pedido.tipo == TipoPedido.ABONO:
            fim = inicio
        elif n_dias and pedido.tipo != TipoPedido.DIARIA:
            fim = (date.fromisoformat(inicio) + timedelta(days=int(n_dias.group(1)) - 1)).isoformat()
        else:
            fim = _data_iso(pedido.data_fim)
            fim = fim if fim and fim >= inicio else None

    vender = RE_VENDER.search(texto)
    dias_vendidos = min(int(vender.group(1)), 10) if vender else 0

    corrigido = {"matricula": matricula, "data_inicio": inicio, "data_fim": fim, "dias_vendidos": dias_vendidos}
    if pedido.tipo != TipoPedido.DIARIA:  # destino só existe em diária
        corrigido.update(destino=None, tipo_destino=None)
    return pedido.model_copy(update=corrigido)


# ------------------------------------------------------------------ 🆕 A: votação (OPCIONAL, bônus)
# Para ligar no grafo depois de pronto: USAR_VOTACAO = True em etapa07_grafo.py.
# 🆕 A.1 -- três instruções LIGEIRAMENTE diferentes para a MESMA tarefa (comece cada uma com f"Hoje é {texto_hoje()}. ")
#   ex.: um extrator literal; um que confere as datas contra o calendário; um que "pensa como o RH".
INSTRUCOES_VOTANTES: list[str] = []  # TODO 🆕 A.1: preencha com 3 strings


async def extrair_com_votacao(texto: str) -> tuple[Pedido, bool, list[Pedido]]:
    # 🆕 A.2 -- crie os 3 extratores e rode-os AO MESMO TEMPO:
    #     resultados = await asyncio.gather(*(Runner.run(e, texto) for e in extratores))
    #   depois chame votar([r.final_output for r in resultados]) e devolva (pedido_final, divergencia, votos).
    #   Conceito: aula7/exemplos/08_paralelismo/main.py (fan-out/fan-in), aqui em asyncio, sem grafo.
    raise NotImplementedError("🆕 A.2: extrair_com_votacao")


def votar(votos: list[Pedido]) -> tuple[Pedido, bool]:
    """(PRONTO) Maioria por campo. Sem maioria (2 de 3) em `tipo` ou nas datas -> divergencia=True: o sistema vai
    pedir esclarecimento em vez de adivinhar. Os campos não votados vêm do primeiro voto."""
    decidido, divergencia = {}, False
    for campo in ("tipo", "matricula", "data_inicio", "data_fim", "dias_vendidos"):
        valor, quantos = Counter(getattr(voto, campo) for voto in votos).most_common(1)[0]
        decidido[campo] = valor
        if quantos < 2 and campo in ("tipo", "data_inicio", "data_fim"):
            divergencia = True
    return votos[0].model_copy(update=decidido), divergencia


def _demo_extracao() -> None:
    for nome in ("C1_abono_simples", "C2_ferias_com_venda", "C5_sem_matricula", "C11_diaria_exterior"):
        pedido = extrair(CASOS[nome]["pedido"])
        print(f"\n{nome}\n  {pedido.model_dump()}")


def _demo_votacao() -> None:
    for nome in ("C1_abono_simples", "C2_ferias_com_venda", "C9_divergencia"):
        texto = CASOS[nome]["pedido"]
        inicio = time.perf_counter()
        pedido, divergencia, votos = asyncio.run(extrair_com_votacao(texto))
        segundos = time.perf_counter() - inicio
        print(f"\n{nome}  ({segundos:.1f}s, divergencia={divergencia})")
        for i, voto in enumerate(votos, 1):
            print(f"  voto {i}: tipo={voto.tipo.value} {voto.data_inicio}..{voto.data_fim} vende={voto.dias_vendidos}")
        print(f"  decisão: {pedido.model_dump()}")
    # (PRONTO) os mesmos 3 extratores EM SEQUÊNCIA, para comparar: o paralelo fica perto do tempo de UMA chamada?
    texto = CASOS["C2_ferias_com_venda"]["pedido"]
    inicio = time.perf_counter()
    for instrucoes in INSTRUCOES_VOTANTES:
        Runner.run_sync(criar_extrator(instrucoes), texto)
    print(f"\nC2 em sequência: {time.perf_counter() - inicio:.1f}s (compare com o tempo do C2 em paralelo, acima)")


if __name__ == "__main__":
    print(f"[modelo] {MODELO}")
    _demo_votacao() if "--votacao" in sys.argv else _demo_extracao()