FROM maven:3.9-eclipse-temurin-21 AS build
WORKDIR /workspace

COPY backend/pom.xml backend/pom.xml
RUN mvn -B -ntp -f backend/pom.xml dependency:go-offline
COPY backend/src backend/src
COPY docs/contracts/fixtures/supplier-chat.json docs/contracts/fixtures/supplier-chat.json
RUN mvn -B -ntp -f backend/pom.xml -Dmaven.test.skip=true package

FROM eclipse-temurin:21-jre
WORKDIR /app
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 negopilot \
    && useradd --uid 10001 --gid negopilot --no-create-home --shell /usr/sbin/nologin negopilot
COPY --from=build --chown=10001:10001 /workspace/backend/target/negopilot-backend-0.1.0-SNAPSHOT.jar /app/app.jar
USER 10001:10001
EXPOSE 8080
ENTRYPOINT ["java"]
CMD ["-jar", "/app/app.jar"]
