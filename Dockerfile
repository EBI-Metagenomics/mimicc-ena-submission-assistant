# Pinned refs for sibling repos this image pulls at build time. Bump these
# (and the matching git+https pins in pyproject.toml) when a sibling repo
# cuts a new tag — see README "Pinned dependency versions".
ARG DATAHARMONIZER_REF=v2.1.1-mimicc
ARG DH_BUILDER_REF=v0.1.0

# Pinned sibling-repo sources, replacing the old additional_contexts/vendor.sh
# local-checkout mechanism. Each is a shallow clone at a fixed tag.
FROM alpine/git:latest AS dataharmonizer-src
ARG DATAHARMONIZER_REF
RUN git clone --branch "${DATAHARMONIZER_REF}" --depth 1 \
      https://github.com/EBI-Metagenomics/DataHarmonizer.git /src

FROM alpine/git:latest AS dh-builder-src
ARG DH_BUILDER_REF
RUN git clone --branch "${DH_BUILDER_REF}" --depth 1 \
      https://github.com/EBI-Metagenomics/dh-builder.git /src

# Builds the embedded DataHarmonizer (DH) bundle from the pinned DataHarmonizer
# checkout above. Mirrors scripts/build_dh_template.sh. dh_build_steps.sh comes
# from the pinned dh-builder checkout — see
# https://github.com/EBI-Metagenomics/dh-builder.
FROM node:20-slim AS dh-builder
RUN apt-get update && apt-get install -y python3 python3-pip && rm -rf /var/lib/apt/lists/*

COPY --from=dataharmonizer-src /src /dh-src
RUN pip install --no-cache-dir --break-system-packages -r /dh-src/requirements.txt

# The MIMICC LinkML schema(s) are committed in this repo's schemas/ —
# copy the whole directory (not a single named file) so this step doesn't
# fail if mimicc_experiment.yaml is ever absent.
COPY schemas/ /tmp/schemas/
COPY --from=dh-builder-src /src/scripts/dh_build_steps.sh /tmp/dh_build_steps.sh
# Sample (mimicc_sample.yaml) and experiment (mimicc_experiment.yaml) are two
# separate templates — see README "Experiment metadata schema". The
# experiment template builds alongside the sample one if its schema file is
# present, so the image build never breaks if it's ever missing.
# Stage every template with DH_SKIP_BUILD=1 (defers the one expensive
# yarn build:web), then run the actual build once on the final invocation so
# all staged folders — mimicc, mimicc_experiment and study — end up in the
# bundle. The study folder is the fixed template slot the Studies tab points
# at (app/schema_service.py: ROLE_FOLDERS["study"]); without it, selecting
# a study schema fails and the study grid never loads.
RUN DH_SKIP_BUILD=1 bash /tmp/dh_build_steps.sh /dh-src /tmp/schemas/mimicc_sample.yaml mimicc && \
    if [ -f /tmp/schemas/mimicc_experiment.yaml ]; then \
      DH_SKIP_BUILD=1 bash /tmp/dh_build_steps.sh /dh-src /tmp/schemas/mimicc_experiment.yaml mimicc_experiment; \
    fi && \
    bash /tmp/dh_build_steps.sh /dh-src /tmp/schemas/SRA_study.yaml study

# Builds the static site (scripts/build_dist.py): app scripts, the Python the
# browser runs (app.zip — pinned EBI packages + server modules), schemas, XSDs,
# the DataHarmonizer bundle from the stage above, and config.json. Nothing from
# this stage runs at runtime.
FROM python:3.11-slim AS site-builder

# git is needed for pip's pinned git+https sibling dependencies.
RUN apt-get update && apt-get install -y --no-install-recommends git && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml .
RUN pip install --no-cache-dir .

COPY schemas/ schemas/
COPY assets/ena_schema/ assets/ena_schema/
COPY app/ app/
COPY scripts/build_py_bundle.py scripts/build_dist.py scripts/
COPY --from=dh-builder /dh-src/web/dist/ /dh-bundle/
# Placeholders, not values: the runtime stage substitutes HELPER_PORT/DHTB_URL
# when the container starts, so one image serves any deployment.
RUN HELPER_PORT='${HELPER_PORT}' DHTB_URL='${DHTB_URL}' \
    python scripts/build_dist.py --out /dist --dh /dh-bundle && \
    mv /dist/config.json /dist/config.json.template

# Runtime: a static file server and nothing else.
FROM nginx:1.29-alpine

COPY docker/nginx.conf /etc/nginx/conf.d/default.conf
COPY docker/write-config.sh /docker-entrypoint.d/40-write-config.sh
RUN chmod +x /docker-entrypoint.d/40-write-config.sh
COPY --from=site-builder /dist/ /usr/share/nginx/html/

ENV HELPER_PORT=9100 DHTB_URL=http://localhost:8765
EXPOSE 9000
