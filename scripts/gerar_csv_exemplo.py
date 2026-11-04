"""Gera samples/contatos_100.csv com 100 contatos FICTÍCIOS para testar a importação.

Uso: python scripts/gerar_csv_exemplo.py
"""
import csv
import random
from pathlib import Path

random.seed(42)
NOMES = ["Ana", "Bruno", "Carla", "Diego", "Elisa", "Fábio", "Gisele", "Hugo", "Iara", "João", "Karen", "Lucas",
         "Marina", "Nelson", "Olívia", "Paulo", "Quitéria", "Rafael", "Sara", "Tiago"]
SOBRENOMES = ["Silva", "Souza", "Oliveira", "Santos", "Lima", "Pereira", "Costa", "Ribeiro", "Almeida", "Gomes"]
DEPTOS = {
    "Marketing e Comunicação": ["Gerente de Marketing", "Coordenador de Comunicação"],
    "RH e Gente": ["Gerente de RH", "Analista de Recrutamento"],
    "Comercial e Vendas": ["Diretor Comercial", "Gerente de Vendas"],
    "Diretoria": ["Diretor Geral", "Sócio-Administrador"],
    "Eventos": ["Coordenador de Eventos"],
}

destino = Path(__file__).resolve().parent.parent / "samples" / "contatos_100.csv"
destino.parent.mkdir(exist_ok=True)
with destino.open("w", newline="", encoding="utf-8-sig") as f:
    w = csv.writer(f)
    w.writerow(["nome_completo", "email", "cargo", "departamento", "cnpj", "dominio"])
    for i in range(100):
        nome, sobre = random.choice(NOMES), random.choice(SOBRENOMES)
        depto = random.choice(list(DEPTOS))
        dominio = f"empresa{i % 25 + 1}.com.br"
        w.writerow([f"{nome} {sobre}", f"{nome.lower()}.{sobre.lower()}{i}@{dominio}".replace("í", "i").replace("á", "a"),
                    random.choice(DEPTOS[depto]), depto, "", dominio])
print("Gerado:", destino)
