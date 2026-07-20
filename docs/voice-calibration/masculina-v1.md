# Calibração de Voz: masculina-v1

## Status

`masculina-v1` é o perfil masculino oficial adicional do renderizador local
`chatterbox-multilingual-v3-pt-br`. Ele não substitui `feminina-v1`.

A decisão foi concluída em 17 de julho de 2026 após comparação cega dos quatro
finalistas em narração, diálogo e fala com datas, horários e valores. O registro
estruturado está em
`plugins/audiobook-codex/assets/voices/masculina-v1.promotion.json`.

## Entradas Imutáveis

- Referência: `plugins/audiobook-codex/assets/voices/Masculina.mp3`
- SHA-256:
  `d660006b44018d1e3e4d2aeb287109f53df20810322cceac558b5a8cc44797c3`
- Corpus:
  `E:\Pessoal\e-books\_voice-calibration-masculina\validation-corpus\corpus.json`
- SHA-256 do corpus:
  `bd45c68e3b5fbdc64079e90a71cdd2263f637eaa584052ff0ed273ba7fc64dff`
- Decisão final:
  `E:\Pessoal\e-books\_voice-calibration-masculina\selection\final-selection.json`
- SHA-256 da decisão:
  `7118511e73ef93e29e3187302a324d975b0068b5cb0c1777aea4f53e64e4ce88`

## Método

Sete finalistas foram renderizados pelo mesmo caminho de inicialização usado em
produção:

1. carregar o modelo;
2. definir a seed do segmento;
3. gerar com a referência em `audio_prompt_path`.

Cada candidato foi comparado aos três alvos. O ranking métrico usou:

```text
robustez = 0.7 * média dos composites + 0.3 * menor composite
```

As métricas foram usadas para formar o conjunto de escuta, não para substituir a
decisão humana. O candidato A venceu os três prompts na revisão cega e foi
resolvido como `seed54321-base`, que ocupava a quarta posição métrica.

## Perfil Aprovado

```text
nome: masculina-v1
motor: chatterbox-multilingual-v3-pt-br
runtime: chatterbox-tts 0.1.7
dispositivo: cuda
max_chars: 320
silence_seconds: 0.22
exaggeration: 0.5
cfg_weight: 0.35
temperature: 0.8
repetition_penalty: 1.2
min_p: 0.05
top_p: 1.0
seed: 54321
conditioning_strategy: audio-prompt-per-generate-v1
seed_strategy: fixed-per-segment-v1
```

Cada segmento começa com a mesma seed `54321` aprovada nos três prompts. Seeds
alternativas são reservadas aos retries determinísticos de uma geração rejeitada;
o índice do segmento não altera mais a primeira tentativa do perfil masculino.

| Prompt | Composite | Duração | WAV SHA-256 |
| --- | ---: | ---: | --- |
| `01-narracao` | 0,775640 | 22,160 s | `9e7dded736aa1d8abfc39a5946551e5543f88e332e775165d309e8a11f015eed` |
| `02-dialogo` | 0,605084 | 18,920 s | `40446f04a6f365c388f6a689c5968cec9109e37d4f478b99bb08a3914a7d30ec` |
| `03-semiotica` | 0,656872 | 17,960 s | `777b4121bebff30650c834a7b29956ef1f4e89c743603cad226c3ad5ce108aaa` |

Resultado métrico do candidato aprovado:

```text
robustez: 0.656964
média: 0.679199
mínimo: 0.605084
desvio padrão: 0.071397
```

## Revisão Auditiva

O usuário avaliou os pares sem conhecer a configuração:

- candidato A foi o melhor em narração;
- candidato A foi o melhor em diálogo;
- candidato A foi o melhor no prompt semiótico/numérico.

O registro está em
`E:\Pessoal\e-books\_voice-calibration-masculina\selection\listening-review.json`.
A decisão humana prevaleceu sobre a ordenação automática, conforme o protocolo.

## Reprodução e Produção

Os três WAVs foram reproduzidos em uma segunda execução e permaneceram idênticos
byte a byte. O smoke final foi executado diretamente pelo perfil nomeado:

```powershell
$python = 'E:\Pessoal\tts\chatterbox-multilingual-v3\venv\Scripts\python.exe'

& $python .\plugins\audiobook-codex\scripts\render_chatterbox.py `
  --input-file E:\Pessoal\e-books\_voice-calibration-masculina\validation-corpus\01-narracao.txt `
  --output-dir E:\Pessoal\e-books\_voice-calibration-masculina\selection\production-smoke-masculina-v1-fixed-seed-v2 `
  --standalone --format mp3 --voice-profile masculina-v1 --device cuda
```

O manifesto declarou `profile: masculina-v1` e
`seed_strategy: fixed-per-segment-v1`. A reprodução posterior à correção
manteve os três arquivos de áudio idênticos byte a byte à promoção original;
somente o manifesto mudou para registrar a estratégia e o novo renderer.
Evidência:

- WAV master 1,0x:
  `9e7dded736aa1d8abfc39a5946551e5543f88e332e775165d309e8a11f015eed`
- WAV de publicação 1,2x:
  `9a1881c92d8943bdc590221b42f3f40c1f6cffe8757c05159c2f8840ac7f3d01`
- MP3:
  `47c8653ae1e024f478b5c817c0f012c9914194408e39f9ebb6413dc6b3aa41fd`

O WAV master do perfil é idêntico ao áudio aprovado na seleção. Nenhum EQ,
compressor, de-esser, limiter ou loudness processing foi adotado. A aceleração
de publicação preservando pitch continua sendo uma etapa de entrega separada.

## Uso

```powershell
& $python .\plugins\audiobook-codex\scripts\render_chatterbox.py `
  --input-file <texto-locutor.txt> `
  --output-dir <audio> `
  --standalone --voice-profile masculina-v1 --device cuda
```

`--voice-profile` não pode ser combinado com alterações individuais de referência,
seed ou parâmetros. Isso preserva a identidade calibrada. Sem a opção, o perfil
feminino existente continua sendo o padrão.
