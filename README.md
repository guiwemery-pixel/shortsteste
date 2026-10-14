# CorteCerto ✂️

Site que transforma vídeos longos do YouTube em **Shorts de até 1min30**. Você cola o link, a
plataforma encontra os melhores momentos e entrega os cortes prontos para Shorts, Reels e TikTok.

## O que ele faz

1. **Lê o vídeo**: título, descrição, capítulos e o mapa de **"Mais repetidos"** do YouTube.
2. **Pega a transcrição**: legenda manual ou automática do próprio YouTube. Se o vídeo não tiver
   legenda, a transcrição local com Whisper é opcional (veja abaixo).
3. **Escolhe os melhores momentos**:
   - **Com IA (Claude)**: lê a transcrição inteira e escolhe trechos que funcionam sozinhos, com
     gancho no começo e sem cortar frase no meio. Também gera título, frase de gancho, motivo da
     escolha, nota de 1 a 100 e uma **legenda pronta para o post** com hashtags.
   - **Modo automático** (sem chave de API ou se a IA falhar): usa os trechos mais revistos do
     YouTube e, se não houver, a intensidade da fala.
4. **Gera os cortes** com ffmpeg, cada um com **no máximo 90 segundos**:
   - **Vertical 9:16** com fundo desfocado, **vertical recortado**, **tela dividida** ou formato original;
   - **Legendas no vídeo** no estilo Shorts (poucas palavras por vez, em caixa alta);
   - **Legenda em arquivo `.srt`** para baixar e usar em qualquer editor ou rede social;
   - **Cortar silêncios**: remove as pausas sem fala e junta as partes faladas, com a legenda sincronizada;
   - **Espelhar vídeo**: inverte a imagem horizontalmente;
   - **Tela dividida**: o corte fica em cima e um vídeo que prende a atenção fica embaixo. Já vêm
     três fundos animados gerados pelo próprio ffmpeg (Fractal hipnótico, Jogo da vida, Gradiente
     suave), e você pode **enviar os seus vídeos de fundo** pelo site.

> **Atenção aos direitos autorais.** Use vídeos seus ou que você tem autorização para reutilizar
> (por exemplo, programas oficiais de cortes de canais e podcasts). Espelhar a imagem, cortar
> silêncios ou usar tela dividida **não torna legal** repostar o vídeo de outra pessoa, e não
> impede o YouTube/TikTok de identificar o conteúdo original. O mesmo vale para os vídeos de fundo:
> use gravações suas ou de bancos de vídeo livres de direitos.

## Rodando no seu computador

Requisitos: **Python 3.11+** e **ffmpeg** (com libass, que já vem nas instalações comuns).

```bash
git clone https://github.com/guiwemery-pixel/shortsteste
cd shortsteste
python3 -m venv .venv
source .venv/bin/activate          # no Windows: .venv\Scripts\activate
pip install -r requirements.txt

export ANTHROPIC_API_KEY=sk-ant-...   # opcional: ativa a escolha dos momentos com IA
python -m app
```

Abra <http://localhost:8000>.

O `requirements.txt` já instala o **Deno** (runtime JavaScript que o yt-dlp exige para o YouTube).

### Com Docker

```bash
docker build -t cortecerto .
docker run -p 8000:8000 -e ANTHROPIC_API_KEY=sk-ant-... -v cortecerto-data:/data cortecerto
```

## Configuração (variáveis de ambiente)

| Variável | Padrão | Para que serve |
| --- | --- | --- |
| `ANTHROPIC_API_KEY` | (vazio) | Ativa a seleção com Claude. Sem ela, o site usa o modo automático. |
| `CLAUDE_MODEL` | `claude-opus-5-5` | Modelo usado para escolher os momentos. |
| `CLAUDE_EFFORT` | `high` | Profundidade da análise (`low`, `medium`, `high`, `xhigh`, `max`). |
| `YTDLP_COOKIES_FILE` | (vazio) | Arquivo de cookies do YouTube (formato Netscape) para vídeos que pedem login. |
| `MAX_VIDEO_MINUTES` | `180` | Duração máxima do vídeo aceito. |
| `MAX_DOWNLOAD_HEIGHT` | `1080` | Resolução máxima baixada do YouTube. |
| `OUTPUT_WIDTH` | `1080` | Largura dos cortes (1080 gera 1080×1920). |
| `MAX_BACKGROUND_MB` | `500` | Tamanho máximo de cada vídeo de fundo enviado. |
| `WORKERS` | `1` | Quantos vídeos são processados ao mesmo tempo. |
| `JOB_TTL_HOURS` | `24` | Depois desse tempo os cortes são apagados. |
| `DATA_DIR` | `./data` | Onde ficam os cortes e os vídeos de fundo. |
| `WHISPER_MODEL` | (vazio) | Ex.: `small`. Transcreve vídeos sem legenda (requer `pip install faster-whisper`). |
| `HOST` / `PORT` | `0.0.0.0` / `8000` | Endereço do servidor. |

### "Sign in to confirm you're not a bot"

Em servidores na nuvem, o YouTube às vezes pede login. Exporte os cookies de uma conta sua com uma
extensão de navegador ("Get cookies.txt"), salve o arquivo no servidor e aponte
`YTDLP_COOKIES_FILE` para ele.

## Como o código está organizado

```
app/
  main.py         API (FastAPI) e arquivos do site
  jobs.py         fila em segundo plano e o passo a passo do processamento
  youtube.py      yt-dlp: validação do link, metadados, legendas e download
  transcript.py   leitura das legendas (json3 e WebVTT) com tempo por palavra
  highlights.py   escolha dos momentos (Claude + modo automático) e ajuste dos cortes
  captions.py     legendas queimadas (ASS) e arquivo .srt
  video.py        ffmpeg: formatos, espelhamento, tela dividida, cortes de silêncio
  backgrounds.py  biblioteca de vídeos de fundo (prontos e enviados)
  whisper.py      transcrição local opcional
static/           página (HTML, CSS e JavaScript, sem build)
tests/            testes automatizados (pytest)
```

### API

- `POST /api/jobs`: `{url, max_clips (1–10), max_duration (15–90), format, captions, mirror, remove_silence, background}`
- `GET /api/jobs/{id}`: andamento e cortes prontos (MP4, capa, `.srt`, legenda do post)
- `GET /api/backgrounds`, `POST /api/backgrounds` (envio de arquivo), `DELETE /api/backgrounds/{id}`
- `GET /api/health`

## Testes

```bash
pip install -r requirements-dev.txt
python -m pytest
```

Os testes não acessam o YouTube nem a API do Claude: usam um vídeo gerado pelo ffmpeg e um cliente
falso do Claude. Eles cobrem o fluxo completo (link → transcrição → momentos → cortes → download),
a tela dividida, o espelhamento, o corte de silêncios e o envio de vídeos de fundo.
