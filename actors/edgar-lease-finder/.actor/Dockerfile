FROM apify/actor-node:22
COPY package*.json ./
RUN npm --quiet install --omit=dev --no-optional && npm cache clean --force
COPY . ./
CMD npm start --silent
