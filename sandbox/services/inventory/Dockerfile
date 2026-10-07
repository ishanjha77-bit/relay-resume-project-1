# syntax=docker/dockerfile:1
FROM eclipse-temurin:25-jdk AS build
WORKDIR /src
COPY .mvn .mvn
COPY mvnw pom.xml ./
RUN --mount=type=cache,target=/root/.m2 sh ./mvnw -B -q dependency:go-offline
COPY src src
RUN --mount=type=cache,target=/root/.m2 sh ./mvnw -B -q package -DskipTests \
 && java -Djarmode=tools -jar target/*.jar extract --layers --launcher --destination /out

FROM eclipse-temurin:25-jre
ARG OTEL_AGENT_VERSION=2.32.0
ARG OTEL_AGENT_SHA256=f787eb6c7f3d18e69a431e108a15278d25ee37f83d68b678f621e063f3988f82
# Pre-create /otel: ADD --chmod would otherwise create it as 644 (not traversable).
RUN mkdir -p /otel && useradd --system --uid 10001 app
ADD --chmod=644 --checksum=sha256:${OTEL_AGENT_SHA256} \
    https://github.com/open-telemetry/opentelemetry-java-instrumentation/releases/download/v${OTEL_AGENT_VERSION}/opentelemetry-javaagent.jar \
    /otel/opentelemetry-javaagent.jar
WORKDIR /app
COPY --from=build /out/dependencies/ ./
COPY --from=build /out/spring-boot-loader/ ./
COPY --from=build /out/snapshot-dependencies/ ./
COPY --from=build /out/application/ ./
USER 10001
EXPOSE 8080
ENTRYPOINT ["java", "org.springframework.boot.loader.launch.JarLauncher"]
