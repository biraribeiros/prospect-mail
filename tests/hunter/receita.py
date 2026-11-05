"""Gera arquivos pequenos no layout da base CNPJ da Receita (ISO-8859-1, ';', sem cabeçalho)."""
import csv
import io
import zipfile
from pathlib import Path


def dvs(base12: str) -> str:
    def calc(base: str) -> str:
        pesos = [6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2][-len(base):]
        r = sum(int(d) * p for d, p in zip(base, pesos)) % 11
        return "0" if r < 2 else str(11 - r)

    d1 = calc(base12)
    return d1 + calc(base12 + d1)


def cnpj_de(basico: str, ordem: str = "0001") -> str:
    return basico + ordem + dvs(basico + ordem)


def estab(basico, *, matriz="1", fantasia="", situacao="02", abertura="20150314", cnae="7020400",
          sec="6202300,7490104", uf="SP", mun="7107", ddd="11", tel="35551234", email="contato@contador.com.br"):
    cnpj = cnpj_de(basico)
    r = [""] * 30
    r[0], r[1], r[2], r[3], r[4], r[5] = basico, cnpj[8:12], cnpj[12:], matriz, fantasia, situacao
    r[10], r[11], r[12] = abertura, cnae, sec
    r[13], r[14], r[15], r[17], r[18] = "RUA", "DAS FLORES", "100", "CENTRO", "01000000"
    r[19], r[20], r[21], r[22], r[27] = uf, mun, ddd, tel, email
    return r


def empresa(basico, razao, porte="01"):
    return [basico, razao, "2062", "49", "10000,00", porte, ""]


def socio(basico, nome, tipo="2", qualif="49"):
    return [basico, tipo, nome, "***123456**", qualif, "20150314", "", "", "", "", "3"]


def _csv_bytes(rows):
    buf = io.StringIO()
    csv.writer(buf, delimiter=";", quoting=csv.QUOTE_ALL, lineterminator="\n").writerows(rows)
    return buf.getvalue().encode("latin-1")


def write_set(folder: Path, *, zipped=False) -> dict:
    """Cenário: 2 empresas dentro do ICP (CNAE 70.20-4, SP, ME/EPP) e 5 que devem ser descartadas."""
    folder.mkdir(parents=True, exist_ok=True)
    estabs = [
        estab("11111111", fantasia="ALFA CONSULTORIA"),                       # dentro (ME)
        estab("22222222", situacao="08"),                                     # baixada
        estab("33333333", uf="RJ", mun="6001"),                               # outro estado
        estab("44444444", cnae="6201500"),                                    # outro CNAE
        estab("55555555", matriz="2"),                                        # filial
        estab("66666666"),                                                    # porte DEMAIS
        estab("77777777", fantasia="BETA GESTAO", mun="6291", email=""),      # dentro (EPP), Campinas
    ]
    empresas = [empresa("11111111", "ALFA CONSULTORIA LTDA", "01"), empresa("22222222", "BAIXADA SA"),
                empresa("33333333", "CARIOCA LTDA"), empresa("44444444", "OUTRO RAMO LTDA"),
                empresa("55555555", "FILIAL LTDA"), empresa("66666666", "GRANDE SA", "05"),
                empresa("77777777", "BETA GESTAO EMPRESARIAL EPP", "03")]
    socios = [socio("11111111", "MARIA DA SILVA", qualif="49"), socio("11111111", "JOSE SANTOS", qualif="22"),
              socio("11111111", "HOLDING PARTICIPACOES LTDA", tipo="1"), socio("77777777", "ANA LIMA", qualif="05"),
              socio("66666666", "FULANO GRANDE")]
    munis = [["7107", "SAO PAULO"], ["6291", "CAMPINAS"], ["6001", "RIO DE JANEIRO"]]
    nomes = {"K3241.K03200Y0.D41011.ESTABELE": estabs, "K3241.K03200Y0.D41011.EMPRECSV": empresas,
             "K3241.K03200Y0.D41011.SOCIOCSV": socios, "F.K03200$Z.D41011.MUNICCSV": munis}
    for nome, rows in nomes.items():
        data = _csv_bytes(rows)
        if zipped:
            with zipfile.ZipFile(folder / f"{nome.split('.')[-1]}.zip", "w") as z:
                z.writestr(nome, data)
        else:
            (folder / nome).write_bytes(data)
    return {"dentro": ["11111111", "77777777"]}
