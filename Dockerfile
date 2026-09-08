FROM ubuntu:24.04
ENV DEBIAN_FRONTEND=noninteractive

# Systempakete, KiCad 8 PPA, Java und ngspice installieren
RUN apt-get update && apt-get install -y \
    software-properties-common \
    wget \
    curl \
    jq \
    git \
    python3 \
    python3-pip \
    python3-venv \
    openjdk-17-jre-headless \
    ngspice \
    libngspice0 \
    && add-apt-repository --yes ppa:kicad/kicad-8.0-releases \
    && apt-get update \
    && apt-get install -y kicad --no-install-recommends \
    && rm -rf /var/lib/apt/lists/*

# Dynamischer Download mit DNS-Retries und Fallback auf explizite Version
RUN DOWNLOAD_URL=$(curl -s --retry 3 https://api.github.com/repos/freerouting/freerouting/releases/latest \
    | jq -r '[.assets[] | select(.name | endswith(".jar")) | .browser_download_url][0]') \
    && if [ -z "$DOWNLOAD_URL" ] || [ "$DOWNLOAD_URL" = "null" ]; then \
         echo "API Fallback aktiviert..."; \
         DOWNLOAD_URL="https://github.com/freerouting/freerouting/releases/download/v2.4.1/freerouting-2.4.1.jar"; \
       fi \
    && curl -L --retry 5 --retry-connrefused -o /opt/freerouting.jar "$DOWNLOAD_URL"

# Python Virtual Environment einrichten (PEP 668)
ENV VIRTUAL_ENV=/opt/venv
RUN python3 -m venv $VIRTUAL_ENV
ENV PATH="$VIRTUAL_ENV/bin:$PATH"

# Python-Bibliotheken für EDA, UI und SPICE-Simulation installieren
RUN pip install --no-cache-dir skidl ezdxf ollama pandas gradio pyspice

WORKDIR /app
COPY app.py /app/app.py

EXPOSE 7860
CMD ["python3", "/app/app.py"]
