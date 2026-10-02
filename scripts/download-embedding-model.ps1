# Скачивает bge-m3-Q8_0.gguf (RAG-эмбеддинги) в models/embeddings/.
$ErrorActionPreference = 'Stop'
$dir = Join-Path $PSScriptRoot '..\models\embeddings'
New-Item -ItemType Directory -Force -Path $dir | Out-Null
$out = Join-Path $dir 'bge-m3-Q8_0.gguf'
if (Test-Path $out) {
    Write-Output "already exists: $out"
    exit 0
}
curl.exe -L --fail --show-error -o $out "https://huggingface.co/gpustack/bge-m3-GGUF/resolve/main/bge-m3-Q8_0.gguf"
Write-Output "downloaded: $out"
