FROM node:22-alpine
WORKDIR /srv
RUN chown node:node /srv
USER node
COPY --chown=node:node package.json package-lock.json ./
RUN npm ci --ignore-scripts
# Copia explícita de los archivos que necesita Vite.
COPY --chown=node:node index.html vite.config.js ./
COPY --chown=node:node src ./src
EXPOSE 5173
CMD ["npm", "run", "dev"]
