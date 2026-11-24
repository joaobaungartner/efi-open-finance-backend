$ErrorActionPreference = 'Stop'

function Invoke-TestDocker {
    & docker --host npipe:////./pipe/docker_engine @args
    if ($LASTEXITCODE -ne 0) { throw "Docker retornou código $LASTEXITCODE" }
}

$suffix = [guid]::NewGuid().ToString('N')
$network = "pix-it-$suffix"
$image = "pix-it-runner:$suffix"
$database = "pix_it_$suffix"
$password = [guid]::NewGuid().ToString('N')
$root = (Resolve-Path (Join-Path $PSScriptRoot '../..')).Path
$imageCreated = $false
$networkId = $null
$postgresId = $null

try {
    Invoke-TestDocker build --tag $image --file (Join-Path $PSScriptRoot Dockerfile) $root
    $imageCreated = $true
    Invoke-TestDocker image inspect postgres:16-alpine --format '{{.Id}}' | Out-Null
    $networkId = Invoke-TestDocker network create --internal $network
    if ((Invoke-TestDocker network inspect --format '{{.Internal}}' $networkId) -ne 'true') {
        throw 'A rede Docker de testes precisa ser interna'
    }
    $postgresId = Invoke-TestDocker run --detach --pull never --name "pix-it-db-$suffix" `
        --network $networkId --network-alias pix-postgres --tmpfs /var/lib/postgresql/data `
        --env "POSTGRES_DB=$database" --env "POSTGRES_USER=$database" `
        --env "POSTGRES_PASSWORD=$password" postgres:16-alpine
    Invoke-TestDocker run --rm --pull never --name "pix-it-tests-$suffix" --network $networkId `
        --env "PIX_IT_DATABASE=$database" --env "PIX_IT_PASSWORD=$password" $image
} finally {
    if ($postgresId) { & docker --host npipe:////./pipe/docker_engine rm --force $postgresId }
    if ($networkId) { & docker --host npipe:////./pipe/docker_engine network rm $networkId }
    if ($imageCreated) { & docker --host npipe:////./pipe/docker_engine image rm $image }
}
