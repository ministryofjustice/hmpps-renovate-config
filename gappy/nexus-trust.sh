#!/bin/sh
set -eu

source_ca=/gappy/source-ca.crt
if [ ! -s "$source_ca" ] || grep -q 'PRIVATE KEY' "$source_ca"; then
    printf '%s\n' 'Nexus trust requires an approved public CA certificate bundle, without private keys.' >&2
    exit 1
fi

java_binary=$(readlink -f "$(command -v java)")
java_home=$(dirname "$(dirname "$java_binary")")
work=$(mktemp -d /gappy-trust/.prepare.XXXXXX)
cleanup() {
    for certificate in "$work"/ca-*.pem; do
        if [ -f "$certificate" ]; then
            rm -f "$certificate"
        fi
    done
    rm -f "$work/cacerts"
    rmdir "$work"
}
trap cleanup 0
trap 'exit 1' HUP INT TERM

if ! awk -v directory="$work" '
    {
        sub(/\r$/, "")
        if ($0 == "-----BEGIN CERTIFICATE-----") {
            if (inside) { invalid = 1; exit 1 }
            inside = 1
            count++
            file = directory "/ca-" count ".pem"
        } else if ($0 == "-----END CERTIFICATE-----") {
            if (!inside) { invalid = 1; exit 1 }
            print > file
            close(file)
            inside = 0
            next
        } else if (!inside && $0 !~ /^[[:space:]]*$/) {
            invalid = 1
            exit 1
        }
        if (inside) print > file
    }
    END { if (invalid || inside || count == 0) exit 1 }
' "$source_ca"; then
    printf '%s\n' 'Nexus CA bundle must contain only complete PEM certificates.' >&2
    exit 1
fi

cp "$java_home/lib/security/cacerts" "$work/cacerts"
chmod 600 "$work/cacerts"
count=0
for certificate in "$work"/ca-*.pem; do
    if ! openssl x509 -in "$certificate" -noout -checkend 0 ||
       ! openssl x509 -in "$certificate" -noout -ext basicConstraints | grep -q 'CA:TRUE'; then
        printf '%s\n' 'Nexus trust accepts only valid, unexpired CA certificates.' >&2
        exit 1
    fi
    count=$((count + 1))
    keytool -importcert -noprompt -trustcacerts \
        -alias "gappy-approved-ca-$count" -file "$certificate" \
        -keystore "$work/cacerts" -storepass changeit
done
chmod 644 "$work/cacerts"
mv -f "$work/cacerts" /gappy-trust/cacerts
printf 'Nexus truststore prepared with default JVM roots and %s approved CA certificate(s).\n' "$count"
