"""
🆕 B (OPCIONAL, bônus) -- Hooks para CONTROLAR, não só para observar. E ETAPA 10.1 (obrigatória): o hook que registra.
"""
from agents import AgentsException, RunHooks


class LimiteExcedido(AgentsException):
    """Levantada pelo hook quando um agente sai do que lhe é permitido."""


# 🆕 B.1 -- agente -> conjunto de nomes de tools permitidas.
# Os especialistas usam as tools de etapa06_agentes.py; o Coordenador chama os especialistas.
PERMISSOES: dict[str, set[str]] = {
    "Normas": {"consultar_normas"},
    "Escala": {"consultar_escala"},
    "Financeiro": {"consultar_remuneracao", "tabela_diarias"},
    "Coordenador": {"Normas", "Escala", "Financeiro"},
}


class Governanca(RunHooks):
    def __init__(self, max_tools: int = 6, max_llm: int = 12, verbose: bool = True):
        self.max_tools, self.max_llm, self.verbose = max_tools, max_llm, verbose
        self.tools_chamadas = 0
        self.chamadas_llm = 0
        self.eventos: list[dict] = []  # ETAPA 10.1: o que aconteceu, para gravar em saidas/metricas.jsonl

    async def on_tool_start(self, context, agent, tool) -> None:
        self.tools_chamadas += 1
        
        agent_name = getattr(agent, "name", str(agent))
        tool_name = getattr(tool, "name", str(tool))
        
        # 🆕 B.2 (opcional) -- Validação de permissões e orçamento de tools
        permissoes_agente = PERMISSOES.get(agent_name, set())
        if permissoes_agente and tool_name not in permissoes_agente:
            if self.verbose:
                print(f"[governança] ❌ {agent_name} tentou usar tool não permitida: {tool_name}")
            raise LimiteExcedido(f"Coordenador não pode usar {tool_name}" if agent_name == "Coordenador" else f"Agente {agent_name} sem permissão para {tool_name}")
            
        if self.tools_chamadas > self.max_tools:
            if self.verbose:
                print(f"[governança] ❌ orçamento de tools estourado ({self.tools_chamadas} de {self.max_tools})")
            raise LimiteExcedido(f"Orçamento de tools estourado ({self.tools_chamadas} de {self.max_tools})")

        if self.verbose:
            print(f"[governança] {agent_name} -> {tool_name}: OK")

    async def on_llm_start(self, context, agent, system_prompt, input_items) -> None:
        self.chamadas_llm += 1
        
        # 🆕 B.3 (opcional) -- Validação de orçamento de chamadas ao modelo
        if self.chamadas_llm > self.max_llm:
            agent_name = getattr(agent, "name", str(agent))
            if self.verbose:
                print(f"[governança] ❌ Orçamento de chamadas ao modelo estourado ({self.chamadas_llm}/{self.max_llm})")
            raise LimiteExcedido(f"Orçamento de chamadas ao modelo estourado por {agent_name}: {self.chamadas_llm} de {self.max_llm}")

    async def on_tool_end(self, context, agent, tool, result) -> None:
        # ETAPA 10.1 -- Registra tool, argumentos, resultado (truncado)
        agent_name = getattr(agent, "name", str(agent))
        tool_name = getattr(tool, "name", str(tool))
        
        # Os argumentos estão em context.tool_arguments (texto JSON) quando a tool é function_tool
        args = getattr(context, "tool_arguments", "{}")
        
        resultado_str = str(result)
        resultado_truncado = resultado_str[:200] + "..." if len(resultado_str) > 200 else resultado_str

        self.eventos.append({
            "tipo": "tool_end",
            "agente": agent_name,
            "tool": tool_name,
            "argumentos": args,
            "resultado": resultado_truncado,
        })

    async def on_agent_end(self, context, agent, output) -> None:
        # ETAPA 10.1 -- Registra os tokens consumidos até aqui
        agent_name = getattr(agent, "name", str(agent))
        total_tokens = getattr(getattr(context, "usage", None), "total_tokens", 0)

        self.eventos.append({
            "tipo": "agent_end",
            "agente": agent_name,
            "tokens": total_tokens,
        })